"""粘合性通知：里程碑/连续打卡判定（§10.4.18）。

纯函数判定 + store 计数/登记 + app 成功事件钩子的端到端接线。
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from draw_portal import milestones, subscribe_notify
from draw_portal.store import Store


# ---- 纯函数：里程碑 ----

def test_milestone_for_count_thresholds():
    assert milestones.milestone_for_count(0) is None
    assert milestones.milestone_for_count(1) == 1
    assert milestones.milestone_for_count(9) == 1
    assert milestones.milestone_for_count(10) == 10
    assert milestones.milestone_for_count(51) == 50
    assert milestones.milestone_for_count(99) == 50
    assert milestones.milestone_for_count(100) == 100
    assert milestones.milestone_for_count(501) == 500


def test_milestone_texts_pinned_and_short():
    # 文案钉死（§10.4.18 表）；thing1 上限 20 字、phrase2 上限 5 字
    assert milestones.DRAW_MILESTONE_TEXTS[1] == "第一幅作品"
    assert milestones.draw_milestone_text(10) == "第 10 幅作品"
    for n, text in milestones.DRAW_MILESTONE_TEXTS.items():
        assert len(text) <= 20
    assert len(milestones.streak_milestone_text(30)) <= 20
    assert len(milestones.ENGAGEMENT_PHRASE) <= 5


# ---- 纯函数：连续打卡 ----

def test_streak_for_dates_walk():
    today = date(2026, 9, 12)
    # 今天缺席 → 0（不追溯）
    past = {today - timedelta(days=i) for i in range(1, 4)}
    assert milestones.streak_for_dates(past, today) == 0
    assert milestones.streak_for_dates({today}, today) == 1
    assert milestones.streak_for_dates({today, today - timedelta(days=1)}, today) == 2
    gap = {
        today,
        today - timedelta(days=1),
        today - timedelta(days=3),
        today - timedelta(days=4),
    }
    assert milestones.streak_for_dates(gap, today) == 2


def test_streak_milestone_thresholds():
    assert milestones.streak_milestone_for(0) is None
    assert milestones.streak_milestone_for(2) is None
    assert milestones.streak_milestone_for(3) == 3
    assert milestones.streak_milestone_for(6) == 3
    assert milestones.streak_milestone_for(7) == 7
    assert milestones.streak_milestone_for(30) == 30
    assert milestones.streak_milestone_text(7) == "连续打卡 7 天"


# ---- store：计数 / 日期 / 登记幂等 ----

def _mk_job(store: Store, user_id: str, created_at: float, status: str = "ready") -> None:
    conn = store._connect()
    conn.execute(
        "INSERT INTO draw_jobs (id, user_id, status, created_at) VALUES (?, ?, ?, ?)",
        (f"j{time.monotonic()}", user_id, status, created_at),
    )
    conn.commit()
    conn.close()


def _seed_user(store: Store) -> None:
    conn = store._connect()
    conn.execute(
        "INSERT INTO users (id, email, password_hash, wx_openid, created_at)"
        " VALUES ('u1','a@b.c','x','oX-1', 0)"
    )
    conn.commit()
    conn.close()


def test_store_ready_count_and_days(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    _seed_user(store)
    now = time.time()
    _mk_job(store, "u1", now, status="ready")
    _mk_job(store, "u1", now - 60, status="ready")
    _mk_job(store, "u1", now - 120, status="failed")
    _mk_job(store, "u2", now, status="ready")
    assert store.count_ready_jobs("u1") == 2
    assert store.count_ready_jobs("ghost") == 0
    # 同日两单 → 单一日期
    days = store.list_ready_job_days("u1")
    assert len(days) == 1


def test_record_engagement_event_idempotent(tmp_path: Path):
    store = Store(tmp_path / "t.db")
    assert store.record_engagement_event("u1", "milestone", "draw:1", when=1.0) is True
    assert store.record_engagement_event("u1", "milestone", "draw:1", when=2.0) is False
    assert store.record_engagement_event("u1", "streak", "streak:3", when=3.0) is True


# ---- app 钩子接线：成功事件 → 里程碑/打卡推送（已发登记防重） ----

class _FakeConfig:
    wx_appid = "wx-test-appid"
    wx_secret = "wx-test-secret"
    subscribe_template_id = "TPL-1"


def test_engagement_notify_fires_milestone_and_streak(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "t.db")
    _seed_user(store)
    today = date.today()
    for i in range(3):
        _mk_job(store, "u1", time.mktime((today - timedelta(days=2 - i)).timetuple()))

    sent: list[dict] = []
    monkeypatch.setattr(
        subscribe_notify, "notify_job_result_async", lambda cfg, **kw: sent.append(kw)
    )
    from draw_portal import app as app_module

    app_module._engagement_notify(_FakeConfig(), store, user_id="u1", openid="oX-1")
    titles = sorted(c["title"] for c in sent)
    # 计数 3 ≥ 1 → 最高跨过 draw:1；连续 3 天 ≥ 3 → streak:3；phrase 统一里程碑
    assert titles == sorted(["第一幅作品", "连续打卡 3 天"])
    assert all(c["phrase"] == "里程碑" for c in sent)
    assert all(c["status"] == "success" for c in sent)

    # 已登记，重复调用不再推送
    app_module._engagement_notify(_FakeConfig(), store, user_id="u1", openid="oX-1")
    assert len(sent) == 2


def test_engagement_notify_skips_when_disabled(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "t.db")
    _seed_user(store)
    _mk_job(store, "u1", time.time())
    cfg = _FakeConfig()
    cfg.subscribe_template_id = ""
    sent: list[dict] = []
    monkeypatch.setattr(
        subscribe_notify, "notify_job_result_async", lambda cfg, **kw: sent.append(kw)
    )
    from draw_portal import app as app_module

    app_module._engagement_notify(cfg, store, user_id="u1", openid="oX-1")
    assert sent == []


def test_notify_subscribe_success_triggers_engagement(tmp_path: Path, monkeypatch):
    """成功路径接线：_notify_subscribe(status=success) 必须带起 _engagement_notify。"""
    store = Store(tmp_path / "t.db")
    _seed_user(store)
    _mk_job(store, "u1", time.time())
    calls: list[str] = []
    monkeypatch.setattr(
        subscribe_notify,
        "notify_job_result_async",
        lambda cfg, **kw: calls.append(kw["phrase"] or ""),
    )
    from draw_portal import app as app_module

    ok = app_module._notify_subscribe(
        _FakeConfig(), store, user_id="u1", status="success", title="画一只猫"
    )
    assert ok is True
    # 一条常规成功（phrase 缺省）+ 一条里程碑（phrase=里程碑）
    assert sorted(calls) == ["", "里程碑"]

    failed_calls: list[str] = []
    monkeypatch.setattr(
        subscribe_notify,
        "notify_job_result_async",
        lambda cfg, **kw: failed_calls.append(kw["phrase"] or ""),
    )
    app_module._notify_subscribe(
        _FakeConfig(), store, user_id="u1", status="failed", title="画一只猫"
    )
    # 失败只发常规一条，不触发粘合性通知
    assert failed_calls == [""]
