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
  只进图、不进元数据；隐私见 privacy-policy.md「可选冷备份」条款。
- **worker 不绑定任何 Store**（2026-09-12 评审修复）：Store 实例随任务入队，
  worker 只用任务自带的实例回写——同进程多 app/多库（测试、迁移脚本）不会
  把任务写进第一个库。
- 成功上传追加 telegram_archive_log 台账（upload 时刻写入，行删除不删台账）：
  台账是「分用户批量召回」的唯一凭据——saved_previews 行被 trim/删除后，
  本地与 DB 都没副本，只有台账还留着 file_id→用户 的映射。
"""
from __future__ import annotations

import argparse
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
    user_id: str
    store: Any  # 行所属库：worker 只用任务自带实例回写，绝不全局绑定


def configured() -> bool:
    """已有副本的删除/取回仅要求凭据，不能随新增上传开关一起失效。"""
    return bool(os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHANNEL_ID"))


def enabled() -> bool:
    """三项全配且显式开关打开才启用（默认关，fail-open）。"""
    return (
        os.environ.get("HUTUJI_TG_UPLOAD_ENABLED", "").strip() == "1"
        and configured()
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
    _queue.put(
        _Task(
            saved_id=saved.id, path=path, title=saved.title, user_id=saved.user_id, store=store
        )
    )
    _ensure_worker()


def delete_message_async(saved: Any) -> None:
    """图库删除时尽力撤回频道副本（用户要求删除 → 第三方也不留）。fire-and-forget。"""
    if not configured() or not saved.tg_message_id:
        return
    _delete_channel_copy_async(int(saved.tg_message_id))


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, daemon=True, name="hutuji-tg-archive").start()


def _worker_loop() -> None:
    while True:
        task = _queue.get()
        try:
            _process(task.store, task)
        except Exception as exc:  # noqa: BLE001 通知面永不掀主链。
            logger.warning("tg-archive task failed type=%s", type(exc).__name__)
        finally:
            time.sleep(_SPACING_S)


def _process(store: Any, task: _Task) -> None:
    # 停用新增上传后，已入内存队列的任务也不得继续外发。
    if not enabled():
        return
    try:
        src_sha = hashlib.sha256(task.path.read_bytes()).hexdigest()
    except OSError:
        store.mark_preview_telegram(task.saved_id, state="failed", file_id=None, message_id=None)
        return
    # 入队后行可能已被用户删除（进程慢/重启久）：不再上传，直接放弃。
    if store.get_saved_preview(task.saved_id, user_id=task.user_id) is None:
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
    if not ok:
        if not store.mark_preview_telegram(task.saved_id, state="failed", file_id=file_id, message_id=message_id):
            # 校验异常不撤销用户的删除；本地行已消失时仍须回收已上传副本。
            _delete_channel_copy_async(message_id)
        return
    # 删除/归档竞态收口（2026-09-12 评审①）：mark 返回行是否存在；台账 INSERT
    # 以行仍存在为守卫。任何一步发现行已删 → 立即尽力撤回频道副本，不落台账
    # （recall 以台账为准，无台账 = 不可复活），尊重删除意愿。
    if not store.mark_preview_telegram(
        task.saved_id, state="uploaded", file_id=file_id, message_id=message_id
    ):
        _delete_channel_copy_async(message_id)
        return
    if not store.append_archive_log(
        saved_id=task.saved_id,
        user_id=task.user_id,
        title=task.title,
        preview_path=str(task.path),
        tg_file_id=file_id,
        tg_message_id=message_id,
    ):
        _delete_channel_copy_async(message_id)
        return
    logger.info("tg-archive uploaded saved=%s", task.saved_id)


def _delete_channel_copy_async(message_id: int) -> None:
    """撤回频道副本（尽力，fire-and-forget）；失败仅日志。"""

    def _run() -> None:
        try:
            _api_call(
                "deleteMessage",
                {"chat_id": os.environ["TG_CHANNEL_ID"], "message_id": str(message_id)},
                timeout=_API_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tg-archive delete failed type=%s", type(exc).__name__)

    threading.Thread(target=_run, daemon=True, name="hutuji-tg-archive-delete").start()


def _verify_tmp() -> Path:
    return Path(os.environ.get("DRAW_PORTAL_DATA_DIR", "/tmp")) / "tg-archive-verify.tmp"


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

def _json_or_none(body: str) -> Any:
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None



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


def _download_document(file_id: str, dest: Path) -> bytes | None:
    """getFile → file/bot…/<path> 下载到 dest；返回字节或 None（网络/非 2xx）。"""
    data = _api_call("getFile", {"file_id": file_id}, timeout=_API_TIMEOUT_S)
    file_path = str(data.get("result", {}).get("file_path") or "")
    if not file_path:
        return None
    token = os.environ["TG_BOT_TOKEN"]
    cmd = ["curl", "-sS"]
    proxy = os.environ.get("TG_PROXY", "").strip()
    if proxy:
        cmd += ["--proxy", proxy]
    cmd += [
        "--max-time", str(int(_API_TIMEOUT_S)),
        "-o", str(dest),
        f"https://api.telegram.org/file/bot{token}/{file_path}",
    ]
    try:
        # 预删目标 + 要求退出码 0：curl 失败残留旧文件会导致校验假阳性（评审④）。
        try:
            dest.unlink()
        except FileNotFoundError:
            pass
        proc = subprocess.run(cmd, capture_output=True, timeout=_API_TIMEOUT_S + 5, check=False)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or not dest.is_file():
        return None
    return dest.read_bytes()


def _verify_download(file_id: str, src_sha: str) -> bool:
    tmp = _verify_tmp()
    try:
        blob = _download_document(file_id, tmp)
        if blob is None:
            return False
        return hashlib.sha256(blob).hexdigest() == src_sha
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

# ---------------------------------------------------------------------------
# 分用户批量召回（2026-09-12）：从频道把某用户已归档的预览拉回图库。
# 覆盖三类：本地文件丢失的在库行（就地补文件）、被 trim 清掉的行（重建行，
# 仅预览无 G-code 副本——按 §10.4.5 契约不可再次发送出图）、用户主动删除的
# 行（频道副本已撤 + 台账标记 removed → 召回时跳过，尊重用户删除意愿）。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 重试（评审②）：failed 的归档提供人工重试入口；pending 由启动恢复兜底。
# ---------------------------------------------------------------------------


def retry_user(store: Any, user_id: str) -> dict[str, Any]:
    """把该用户 pending/failed 且文件在场的行重新入队（CLI 人工触发）。"""
    if not enabled():
        return {"user_id": user_id, "enqueued": 0}
    rows = store.list_retryable_previews(user_id)
    for saved in rows:
        enqueue_preview(store, saved)
    return {"user_id": user_id, "enqueued": len(rows)}


def restore_user(store: Any, user_id: str, *, limit: int | None = None) -> dict[str, Any]:
    """按台账召回；返回计数摘要。网络失败逐条记录、继续其余（部分成功语义）。"""
    entries = store.list_archive_log_for_user(user_id)
    if limit is not None:
        entries = entries[:limit]
    summary = {"user_id": user_id, "restored": 0, "recreated": 0, "skipped": 0, "failed": 0}
    tmp = _verify_tmp()
    for entry in entries:
        try:
            _restore_one(store, user_id, entry, tmp, summary)
        except Exception as exc:  # noqa: BLE001 逐条隔离，不整批中断。
            summary["failed"] += 1
            logger.warning("tg-archive restore failed type=%s", type(exc).__name__)
        time.sleep(_SPACING_S)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return summary


def _restore_one(store: Any, user_id: str, entry: dict[str, Any], tmp: Path, summary: dict[str, Any]) -> None:
    saved_id = str(entry["saved_id"])
    file_id = str(entry["tg_file_id"])
    live = store.get_saved_preview(saved_id, user_id=user_id)
    if live is not None:
        if Path(live.preview_path).is_file():
            summary["skipped"] += 1
            return
        dest = Path(live.preview_path)
    else:
        dest = _saved_dir_for(user_id) / f"{saved_id}.png"
    blob = _download_with_retry(file_id)
    if blob is None:
        summary["failed"] += 1
        return
    action = store.restore_saved_preview(
        user_id=user_id,
        saved_id=saved_id,
        preview_path=str(dest),
        preview_bytes=blob,
        tg_file_id=file_id,
        tg_message_id=int(entry["tg_message_id"]),
    )
    summary[action] += 1


def _download_with_retry(file_id: str) -> bytes | None:
    tmp = _verify_tmp()
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            blob = _download_document(file_id, tmp)
        except _RateLimited as exc:
            if attempt >= _MAX_ATTEMPTS:
                return None
            time.sleep(min(exc.retry_after, 30.0))
            continue
        except Exception:  # noqa: BLE001
            return None
        if blob is not None:
            return blob
        time.sleep(_SPACING_S)
    return None


def _saved_dir_for(user_id: str) -> Path:
    """与 app._saved_dir 同构（data_dir/saved/<user_id>）；重建行必须同约定。"""
    data_dir = os.environ.get("DRAW_PORTAL_DATA_DIR", "/var/lib/hutuji/draw-portal")
    return Path(data_dir) / "saved" / user_id


def enqueue_pending_on_boot(store: Any) -> int:
    """启动恢复（评审②）：扫描 tg_state='pending' 且文件在场的行重新入队。
    任务只存在内存队列，入队后进程重启会让行永久卡在 pending——启动时兜底。"""
    if not enabled():
        return 0
    rows = store.list_pending_previews()
    for saved in rows:
        enqueue_preview(store, saved)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="TG 频道归档（分用户召回/重试）")
    parser.add_argument("--db", required=True, help="draw-portal SQLite 路径")
    parser.add_argument("--restore-user", default=None, help="按台账召回该用户")
    parser.add_argument("--retry-user", default=None, help="重试该用户的 pending/failed 归档")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if not configured() or (args.retry_user and not enabled()):
        print("tg-archive disabled（缺凭据或新增上传未启用）")
        return 2
    from draw_portal.store import Store

    store = Store(Path(args.db))
    if args.restore_user:
        summary = restore_user(store, args.restore_user, limit=args.limit)
    elif args.retry_user:
        summary = retry_user(store, args.retry_user)
    else:
        parser.error("需要 --restore-user 或 --retry-user")
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
