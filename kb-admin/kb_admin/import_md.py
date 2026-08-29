from __future__ import annotations

import re
import time
import uuid

from kb_admin.models import Faq

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


def import_candidates(md_text: str) -> list[Faq]:
    lines = md_text.splitlines()
    candidates: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_body
        if current_title is None:
            return
        body = "\n".join(current_body).strip()
        candidates.append((current_title, body.splitlines() if body else []))
        current_title = None
        current_body = []

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current_title = match.group(2).strip()
            current_body = []
            continue
        if current_title is not None:
            current_body.append(line)

    flush()

    now = time.time()
    rows: list[Faq] = []
    for index, (title, body_lines) in enumerate(candidates):
        body = "\n".join(body_lines).strip()
        rows.append(
            Faq(
                id=str(uuid.uuid4()),
                title=title,
                body=body,
                category="other",
                aliases=[],
                enabled=False,
                updated_at=now,
                updated_by="import",
                sort_key=index,
                imported=True,
            )
        )
    return rows
