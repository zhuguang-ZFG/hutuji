from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ("install", "fault", "consumable", "safety", "kxnx", "other")


@dataclass
class Faq:
    id: str
    title: str
    body: str
    category: str
    aliases: list[str]
    enabled: bool
    updated_at: float
    updated_by: str
    sort_key: int
    imported: bool


@dataclass
class PublishRecord:
    id: str
    created_at: float
    created_by: str
    status: str
    xiaozhi_doc_md5: str
    error_summary: str | None
    faq_count: int
