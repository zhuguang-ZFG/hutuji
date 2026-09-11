"""图库预览 PNG 的 Telegram 频道冷备份（PoC，docs/design/telegram-image-host-poc.md）。

- 出口收敛原则同 tg_notify.py：凭据只读进程 env（生产 = /etc/hutuji/draw-portal.env，
  与 /etc/hutuji/tg.env 同源复制），curl 子进程出网（2026-09-12 实测 urllib/httpx
  无 happy-eyeballs、TG AAAA 不可达；curl 经 TG_PROXY 稳定）。
- 门是配件不是闸：HUTUJI_TG_UPLOAD_ENABLED!=1 或三件套缺任一 = 整链禁用，
  enqueue 直接不动作，绝不影响保存主链；任何失败只落日志（记类型，不回显）。
- 落点 = 私有频道（TG_CHANNEL_ID，-100… 形态）：bot 须为成员；本机（阿里云 lima）
  才有代理出网，京东节点不配 UPLOAD_ENABLED 即禁用。
- 三态落库（saved_previews.tg_state）：pending=已入队 / uploaded=上传+校验通过 /
  failed=上传或字节校验失败（本地副本仍在，fail-open）。
- 上传后强制 getFile 下载回读并 sha256 逐字节校验（PoC §5.3 判据 1）；429 按
  retry_after 退避重试 ≤3 次；条间 spacing 1.0s（顺序单 worker，Bot API 宽限内）。
- caption 用固定标记+日期，绝不带用户标题/ID——频道是第三方存储，用户可读文本
  只进图、不进元数据；隐私口径见 draw-portal/privacy-policy.md「可选冷备份」。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bot API sendDocument 上限 20MB（2026-09-12 实测 500KB 上传 3.0s）；预览 PNG
# 常规 ≤1MB，上限留余量防超大上传占住唯一 worker。
_MAX_BYTES = 19 * 1024 * 1024
_UPLOAD_TIMEOUT_S = 300.0
_API_TIMEOUT_S = 30.0
_MAX_ATTEMPTS = 3
_SPACING_S = 1.0

_queue: "queue.Queue[_Task]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


@dataclass(frozen=True)
class _Task:
    saved_id: str
    path: Path
    title: str


def enabled() -> bool:
    """三项全配且显式开关打开才启用（默认关，fail-open）。"""
    return (
        os.environ.get("HUTUJI_TG_UPLOAD_ENABLED", "").strip() == "1"
        and bool(os.environ.get("TG_BOT_TOKEN"))
        and bool(os.environ.get("TG_CHANNEL_ID"))
    )


def enqueue_preview(store: Any, saved: Any) -> None:
    """保存主链调用：未启用/文件缺失/超大 = 静默不动作（行保持原状态）。"""
    if not enabled():
        return
    path = Path(saved.preview_path)
    if not path.is_file():
        return
    try:
        if path.stat().st_size > _MAX_BYTES:
            store.mark_preview_telegram(saved.id, state="failed", file_id=None, message_id=None)
            logger.warning("tg-archive oversize skipped saved=%s", saved.id)
            return
    except OSError:
        return
    _queue.put(_Task(saved_id=saved.id, path=path, title=saved.title))
    _ensure_worker(store)


def delete_message_async(store: Any, saved: Any) -> None:
    """图库删除时尽力撤回频道副本（用户要求删除 → 第三方也不留）。fire-and-forget。"""
    if not enabled() or not saved.tg_message_id:
        return
    message_id = int(saved.tg_message_id)

    def _run() -> None:
        try:
            _api_call("deleteMessage", {"message_id": str(message_id)}, timeout=_API_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 尽力而为；失败仅日志。
            logger.warning("tg-archive delete failed type=%s", type(exc).__name__)

    threading.Thread(target=_run, daemon=True, name="hutuji-tg-archive-delete").start()


def _ensure_worker(store: Any) -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, args=(store,), daemon=True, name="hutuji-tg-archive").start()


def _worker_loop(store: Any) -> None:
    while True:
        task = _queue.get()
        try:
            _process(store, task)
        except Exception as exc:  # noqa: BLE001 通知面永不掀主链。
            logger.warning("tg-archive task failed type=%s", type(exc).__name__)
        finally:
            time.sleep(_SPACING_S)


def _process(store: Any, task: _Task) -> None:
    src_sha = ""
    try:
        src_sha = hashlib.sha256(task.path.read_bytes()).hexdigest()
    except OSError:
        store.mark_preview_telegram(task.saved_id, state="failed", file_id=None, message_id=None)
        return
    file_id = message_id = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            file_id, message_id = _upload_document(task.path)
            break
        except _RateLimited as exc:
            if attempt >= _MAX_ATTEMPTS:
                break
            time.sleep(min(exc.retry_after, 30.0))
        except Exception:  # noqa: BLE001
            break
    if not file_id:
        store.mark_preview_telegram(task.saved_id, state="failed", file_id=None, message_id=None)
        return
    try:
        ok = _verify_download(file_id, src_sha)
    except Exception:  # noqa: BLE001 校验网络失败等同失败态，本地仍在。
        ok = False
    if ok:
        store.mark_preview_telegram(
            task.saved_id, state="uploaded", file_id=file_id, message_id=message_id
        )
        logger.info("tg-archive uploaded saved=%s", task.saved_id)
    else:
        store.mark_preview_telegram(task.saved_id, state="failed", file_id=file_id, message_id=message_id)


class _RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def _curl(*args: str, timeout: float) -> tuple[int, str]:
    """返回 (http_code, body)。curl 是全项目验证过的 TG 出口（见模块 docstring）。"""
    cmd = ["curl", "-sS"]
    proxy = os.environ.get("TG_PROXY", "").strip()
    if proxy:
        cmd += ["--proxy", proxy]
    cmd += ["--max-time", str(int(timeout)), "-w", "\n%{http_code}"] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    out = proc.stdout
    if "\n" not in out:
        return 0, out
    body, _, code = out.rpartition("\n")
    return int(code or 0), body


def _api_call(method: str, fields: dict[str, str], *, timeout: float) -> dict[str, Any]:
    args: list[str] = []
    for key, value in fields.items():
        args += ["-F", f"{key}={value}"]
    token = os.environ["TG_BOT_TOKEN"]
    code, body = _curl(*args, f"https://api.telegram.org/bot{token}/{method}", timeout=timeout)
    if code == 429:
        raise _RateLimited(_retry_after(body))
    data = _json_or_none(body)
    if code != 200 or not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError(f"tg api {method} http={code}")
    return data


def _upload_document(path: Path) -> tuple[str, int | None]:
    data = _api_call(
        "sendDocument",
        {
            "chat_id": os.environ["TG_CHANNEL_ID"],
            "document": f"@{path};type=image/png",
            "caption": f"hutuji-archive {time.strftime('%Y-%m-%d')}",
        },
        timeout=_UPLOAD_TIMEOUT_S,
    )
    doc = data.get("result", {}).get("document", {})
    file_id = str(doc.get("file_id") or "")
    if not file_id:
        raise RuntimeError("tg upload missing file_id")
    message_id = data.get("result", {}).get("message_id")
    return file_id, int(message_id) if isinstance(message_id, int) else None


def _verify_download(file_id: str, src_sha: str) -> bool:
    data = _api_call("getFile", {"file_id": file_id}, timeout=_API_TIMEOUT_S)
    file_path = str(data.get("result", {}).get("file_path") or "")
    if not file_path:
        return False
    token = os.environ["TG_BOT_TOKEN"]
    # 下载落临时文件再校验 sha256（判据：备份必须逐字节一致才算 uploaded）。
    tmp = Path(os.environ.get("DRAW_PORTAL_DATA_DIR", "/tmp")) / "tg-archive-verify.tmp"
    try:
        cmd = ["curl", "-sS"]
        proxy = os.environ.get("TG_PROXY", "").strip()
        if proxy:
            cmd += ["--proxy", proxy]
        cmd += [
            "--max-time", str(int(_API_TIMEOUT_S)),
            "-o", str(tmp),
            f"https://api.telegram.org/file/bot{token}/{file_path}",
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=_API_TIMEOUT_S + 5, check=False)
        except subprocess.TimeoutExpired:
            return False
        if not tmp.is_file():
            return False
        return hashlib.sha256(tmp.read_bytes()).hexdigest() == src_sha
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _retry_after(body: str) -> float:
    try:
        data = json.loads(body)
        params = data.get("parameters") or {}
        return float(params.get("retry_after") or 5.0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 5.0


def _json_or_none(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None
