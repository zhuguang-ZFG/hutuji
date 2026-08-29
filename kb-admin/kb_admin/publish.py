from __future__ import annotations

import importlib.util
import time
import uuid
from pathlib import Path

from kb_admin.assemble import assemble_markdown
from kb_admin.models import PublishRecord
from kb_admin.store import Store


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_xiaozhi_console():
    path = _repo_root() / "scripts" / "xiaozhi_console.py"
    spec = importlib.util.spec_from_file_location("_hutuji_xiaozhi_console", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_console = _load_xiaozhi_console()
update_kb_from_path = _console.update_kb_from_path
XiaozhiConsoleError = _console.XiaozhiConsoleError


def _redact_error(message: str) -> str:
    try:
        from hutuji_mcp.redact import _redact_secret_text

        return _redact_secret_text(message)
    except ImportError:
        redact = getattr(_console, "_redact_text", None)
        if redact is not None:
            return redact(message)
        return message


def publish(
    store: Store,
    *,
    kb_id: int,
    actor: str,
    preface: str,
    appendix: str,
) -> PublishRecord:
    faqs = [f for f in store.list_faqs() if f.enabled]
    body = assemble_markdown(faqs, preface=preface, appendix=appendix)
    tmp_path = store.db_path.parent / "publish_tmp.md"
    tmp_path.write_text(body, encoding="utf-8")

    rec_id = str(uuid.uuid4())
    created_at = time.time()
    faq_count = len(faqs)

    try:
        md5 = update_kb_from_path(kb_id, tmp_path)
        rec = PublishRecord(
            id=rec_id,
            created_at=created_at,
            created_by=actor,
            status="success",
            xiaozhi_doc_md5=md5,
            error_summary=None,
            faq_count=faq_count,
        )
    except XiaozhiConsoleError as exc:
        rec = PublishRecord(
            id=rec_id,
            created_at=created_at,
            created_by=actor,
            status="failed",
            xiaozhi_doc_md5="",
            error_summary=_redact_error(str(exc)),
            faq_count=faq_count,
        )
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 1
        rec = PublishRecord(
            id=rec_id,
            created_at=created_at,
            created_by=actor,
            status="failed",
            xiaozhi_doc_md5="",
            error_summary=_redact_error(f"控制台 API 异常退出 code={code}"),
            faq_count=faq_count,
        )

    store.add_publish_record(rec)
    return rec
