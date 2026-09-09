# Task 2 Report: Catalog + check/upgrade 核心（无 HTTP）

## Status
✅ Complete — 5/5 focused tests pass

## TDD Evidence

### Step 1 — Red (before implementation)

```
$ pytest tests/test_firmware_ota_core.py tests/test_firmware_version.py -q

ImportError: cannot import name 'plan_check' from 'draw_portal.firmware_catalog'
```

### Step 2 — Green (after implementation)

```
$ pytest tests/test_firmware_ota_core.py tests/test_firmware_version.py -q
.....                                                                    [100%]
5 passed in 0.07s
```

## Deliverables

| File | Purpose |
|------|---------|
| `draw-portal/draw_portal/firmware_catalog.py` | `load_catalog`, `recommended_for_board`, `build_bin_url`, `plan_check` |
| `draw-portal/draw_portal/firmware_ota.py` | `assert_upgrade_allowed`（非 idle → busy；board 不符 → board_mismatch） |
| `draw-portal/tests/test_firmware_ota_core.py` | brief 指定 2 项核心测试 |
| `draw-portal/draw_portal/config.py` | 新增 `firmware_catalog_path` / `firmware_public_base` / `firmware_allowed_hosts` / `firmware_signing_secret` |

## Tests

1. `test_plan_check_newer` — catalog 1.0.1 vs 设备 1.0.0 → `update_available=True`
2. `test_busy_and_wrong_board_reject` — streaming→busy、错板→board_mismatch、idle+同板→None
3. `test_firmware_version.py`（Task 1）— 3 项版本解析/比较回归

## Self-review

- `plan_check` 复用 Task 1 的 `is_newer`，未重实现版本比较。
- `build_bin_url` 无 secret 时 `{base}/firmware/{relpath}`；有 secret 时 HMAC-SHA256 hex of `{relpath}:{exp}`。
- Config 新字段均有安全默认值；空 catalog path 不影响现有 `load_config()` 调用方。
- 未实现 HTTP 路由（Task 3 范围）。

## Concerns

- `plan_check` 对 catalog 缺板返回 `reason=board_mismatch`（brief 未测，Task 3 API 需对齐文案）。
- `build_bin_url` / `firmware_allowed_hosts` 尚无单测；Task 3/4 集成时补签名校验与 host 闸测试。
