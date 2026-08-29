from __future__ import annotations

from kb_admin.models import CATEGORIES, Faq

CATEGORY_LABELS: dict[str, str] = {
    "install": "装机",
    "fault": "故障",
    "consumable": "耗材",
    "safety": "安全",
    "kxnx": "奎享",
    "other": "其他",
}


def _format_faq(faq: Faq) -> str:
    parts = [f"### {faq.title}\n"]
    if faq.aliases:
        alias_line = "；".join(faq.aliases)
        parts.append(f"同义问：{alias_line}\n\n")
    parts.append(faq.body.rstrip())
    parts.append("\n\n")
    return "".join(parts)


def assemble_markdown(faqs: list[Faq], *, preface: str, appendix: str) -> str:
    enabled = [f for f in faqs if f.enabled]
    by_category: dict[str, list[Faq]] = {cat: [] for cat in CATEGORIES}
    for faq in enabled:
        if faq.category in by_category:
            by_category[faq.category].append(faq)
        else:
            by_category["other"].append(faq)

    parts: list[str] = []
    if preface:
        parts.append(preface if preface.endswith("\n") else preface + "\n")

    for category in CATEGORIES:
        rows = sorted(by_category[category], key=lambda f: (f.sort_key, f.title))
        if not rows:
            continue
        label = CATEGORY_LABELS[category]
        parts.append(f"## {label}\n\n")
        for faq in rows:
            parts.append(_format_faq(faq))

    if appendix:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(appendix if appendix.endswith("\n") else appendix)

    return "".join(parts)
