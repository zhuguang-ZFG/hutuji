# Task 2 Report: SQLite store + models

## Status
✅ Complete — 6/6 tests pass

## TDD Evidence

### Step 1 — Red (before implementation)

```
$ PYTHONPATH=. python -m pytest tests/test_store.py -q

=================================== ERRORS ====================================
____________________ ERROR collecting tests/test_store.py _____________________
ImportError while importing test module '...\tests\test_store.py'.
...
tests\test_store.py:7: in <module>
    from kb_admin.models import Faq, PublishRecord
E   ModuleNotFoundError: No module named 'kb_admin.models'
=========================== short test summary info ===========================
ERROR tests/test_store.py
1 error in 0.17s
```

### Step 2 — Green (after models.py + store.py)

```
$ PYTHONPATH=. python -m pytest tests/test_store.py -q
......                                                                   [100%]
6 passed in 0.11s
```

## Deliverables

| File | Purpose |
|------|---------|
| `kb-admin/kb_admin/__init__.py` | Empty package init |
| `kb-admin/kb_admin/models.py` | `CATEGORIES`, `Faq`, `PublishRecord` dataclasses |
| `kb-admin/kb_admin/store.py` | `Store` with SQLite schema + CRUD |
| `kb-admin/tests/test_store.py` | 6 focused tests |
| `kb-admin/requirements.txt` | fastapi, uvicorn, pytest |

## Tests

1. `test_upsert_and_list` — brief spec: upsert + category filter + aliases round-trip
2. `test_get_faq` — get by id
3. `test_get_faq_missing` — None for missing id
4. `test_delete_faq` — delete returns bool, idempotent second delete
5. `test_list_faqs_no_filter` — all categories
6. `test_publish_records` — add + list publish records

## Schema

- `faqs`: id PK, aliases stored as JSON text, booleans as INTEGER
- `publish_records`: id PK, ordered by created_at DESC

## Notes

- Tests run with `PYTHONPATH=.` and main hutuji `mcp-server/.venv` (worktree has no local venv yet).
- `list_faqs` orders by `sort_key ASC, updated_at DESC`.
- `upsert_faq` uses SQLite `ON CONFLICT DO UPDATE`.
