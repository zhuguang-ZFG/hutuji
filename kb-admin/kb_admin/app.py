from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from kb_admin.auth import SESSION_COOKIE, clear_session_cookie, set_session_cookie, verify_session
from kb_admin.config import load_config
from kb_admin.import_md import import_candidates
from kb_admin.models import CATEGORIES, Faq
from kb_admin.publish import publish
from kb_admin.store import Store

ACTOR = "admin"


class LoginBody(BaseModel):
    password: str


class FaqCreateBody(BaseModel):
    title: str
    body: str
    category: str = "other"
    aliases: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_key: int = 0


class FaqUpdateBody(BaseModel):
    title: str | None = None
    body: str | None = None
    category: str | None = None
    aliases: list[str] | None = None
    enabled: bool | None = None
    sort_key: int | None = None


def _faq_to_dict(faq: Faq) -> dict[str, Any]:
    return {
        "id": faq.id,
        "title": faq.title,
        "body": faq.body,
        "category": faq.category,
        "aliases": faq.aliases,
        "enabled": faq.enabled,
        "updated_at": faq.updated_at,
        "updated_by": faq.updated_by,
        "sort_key": faq.sort_key,
        "imported": faq.imported,
    }


def _publish_record_to_dict(rec) -> dict[str, Any]:
    return {
        "id": rec.id,
        "created_at": rec.created_at,
        "created_by": rec.created_by,
        "status": rec.status,
        "xiaozhi_doc_md5": rec.xiaozhi_doc_md5,
        "error_summary": rec.error_summary,
        "faq_count": rec.faq_count,
    }


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"无效分类: {category}")


def _read_text(path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def create_app() -> FastAPI:
    config = load_config()
    store = Store(config.db_path)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def auth_dep(
        kb_admin_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> None:
        if not verify_session(config.session_secret, kb_admin_session):
            raise HTTPException(status_code=401, detail="未登录")

    secured = [Depends(auth_dep)]

    @app.post("/api/login")
    def login(body: LoginBody, response: Response) -> dict[str, str]:
        if body.password != config.password:
            raise HTTPException(status_code=401, detail="密码错误")
        set_session_cookie(response, secret=config.session_secret)
        return {"status": "ok"}

    @app.post("/api/logout", dependencies=secured)
    def logout(response: Response) -> dict[str, str]:
        clear_session_cookie(response)
        return {"status": "ok"}

    @app.get("/api/faqs", dependencies=secured)
    def list_faqs(category: str | None = None) -> list[dict[str, Any]]:
        if category is not None:
            _validate_category(category)
        return [_faq_to_dict(f) for f in store.list_faqs(category=category)]

    @app.post("/api/faqs", dependencies=secured)
    def create_faq(body: FaqCreateBody) -> dict[str, Any]:
        _validate_category(body.category)
        faq = Faq(
            id=str(uuid.uuid4()),
            title=body.title,
            body=body.body,
            category=body.category,
            aliases=body.aliases,
            enabled=body.enabled,
            updated_at=time.time(),
            updated_by=ACTOR,
            sort_key=body.sort_key,
            imported=False,
        )
        store.upsert_faq(faq)
        return _faq_to_dict(faq)

    @app.put("/api/faqs/{faq_id}", dependencies=secured)
    def update_faq(faq_id: str, body: FaqUpdateBody) -> dict[str, Any]:
        existing = store.get_faq(faq_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="FAQ 不存在")

        if body.category is not None:
            _validate_category(body.category)

        faq = Faq(
            id=existing.id,
            title=body.title if body.title is not None else existing.title,
            body=body.body if body.body is not None else existing.body,
            category=body.category if body.category is not None else existing.category,
            aliases=body.aliases if body.aliases is not None else existing.aliases,
            enabled=body.enabled if body.enabled is not None else existing.enabled,
            updated_at=time.time(),
            updated_by=ACTOR,
            sort_key=body.sort_key if body.sort_key is not None else existing.sort_key,
            imported=existing.imported,
        )
        store.upsert_faq(faq)
        return _faq_to_dict(faq)

    @app.delete("/api/faqs/{faq_id}", dependencies=secured)
    def delete_faq(faq_id: str) -> dict[str, str]:
        if not store.delete_faq(faq_id):
            raise HTTPException(status_code=404, detail="FAQ 不存在")
        return {"status": "deleted"}

    @app.post("/api/import", dependencies=secured)
    def import_md() -> dict[str, Any]:
        if config.import_md_path is None:
            raise HTTPException(status_code=400, detail="KB_ADMIN_IMPORT_MD 未配置")
        if not config.import_md_path.exists():
            raise HTTPException(status_code=400, detail="导入文件不存在")

        md_text = config.import_md_path.read_text(encoding="utf-8")
        candidates = import_candidates(md_text)
        existing_by_title = {f.title: f for f in store.list_faqs()}

        saved: list[Faq] = []
        for candidate in candidates:
            existing = existing_by_title.get(candidate.title)
            if existing is not None:
                candidate = Faq(
                    id=existing.id,
                    title=candidate.title,
                    body=candidate.body,
                    category=existing.category,
                    aliases=existing.aliases,
                    enabled=existing.enabled,
                    updated_at=candidate.updated_at,
                    updated_by="import",
                    sort_key=candidate.sort_key,
                    imported=True,
                )
            store.upsert_faq(candidate)
            saved.append(candidate)

        return {
            "imported": len(saved),
            "faqs": [_faq_to_dict(f) for f in saved],
        }

    @app.post("/api/publish", dependencies=secured)
    def publish_kb() -> dict[str, Any]:
        preface = _read_text(config.preface_path)
        appendix = _read_text(config.appendix_path)
        rec = publish(
            store,
            kb_id=config.kb_id,
            actor=ACTOR,
            preface=preface,
            appendix=appendix,
        )
        return _publish_record_to_dict(rec)

    @app.get("/api/publish-records", dependencies=secured)
    def list_publish_records() -> list[dict[str, Any]]:
        return [_publish_record_to_dict(r) for r in store.list_publish_records()]

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    uvicorn.run(
        "kb_admin.app:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
    )
