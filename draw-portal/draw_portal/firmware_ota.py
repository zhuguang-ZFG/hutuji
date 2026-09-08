from __future__ import annotations


def assert_upgrade_allowed(
    *,
    job_state: str,
    board: str,
    manifest_board: str,
) -> str | None:
    state = (job_state or "").strip().lower()
    if state != "idle":
        return "busy"
    if board != manifest_board:
        return "board_mismatch"
    return None
