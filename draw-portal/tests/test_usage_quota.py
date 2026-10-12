"""持久配额跨进程互斥、真实路由并发及取消期间预算保留。"""
import asyncio
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import sqlite3
import threading
import time

import httpx
import pytest

from draw_portal.store import Store
from draw_portal import quota
from test_generate import _gen_client, _fake_result, _fake_materialize
from test_chat import _wx_client


def _reserve_in_process(path, user, event):
    return Store(path).reserve_usage(user, event, 1, when=time.time())


def test_quota_is_atomic_across_processes(tmp_path):
    path = tmp_path / 'db.sqlite'
    Store(path)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
        results = list(pool.map(_reserve_in_process, [path]*8, ['u']*8, ['web_generate']*8))
    assert sum(token is not None for token in results) == 1
    store = Store(path)
    assert store.reserve_usage('other', 'web_generate', 1, when=time.time())
    assert store.reserve_usage('u', 'wx_chat', 1, when=time.time())


def test_rolling_window_legacy_events_and_uncertain_reservations(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    now = time.time()
    store.insert_usage_event(source='draw_portal', event_type='web_generate', user_id='u', ok=True)
    assert store.reserve_usage('u', 'web_generate', 1, when=now + 86399) is None
    token = store.reserve_usage('u', 'web_generate', 1, when=now + 86401)
    assert token
    assert Store(tmp_path / 'db.sqlite').reserve_usage('u', 'web_generate', 1, when=now + 86402) is None
    assert store.reserve_usage('u', 'web_generate', 1, when=now + 172802)


def test_failed_settlement_keeps_reservation(tmp_path, monkeypatch):
    store = Store(tmp_path / 'db.sqlite')
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('test failure')
    monkeypatch.setattr(store, 'settle_usage', fail)
    with pytest.raises(sqlite3.OperationalError):
        with quota.slot(store, 'u', 'web_generate', 1, 'full'):
            quota.record(store, source='draw_portal', event_type='web_generate', user_id='u',
                         device_mac=None, detail=None, ok=True)
    assert store.reserve_usage('u', 'web_generate', 1, when=time.time()) is None


@pytest.mark.parametrize('kind', ['generate', 'chat'])
@pytest.mark.parametrize('cancel', [False, True])
def test_paid_route_has_one_owner_until_background_finishes(tmp_path, monkeypatch, kind, cancel):
    entered, release = threading.Event(), threading.Event()
    calls = []
    if kind == 'generate':
        client = _gen_client(monkeypatch, tmp_path, daily_limit=1)
        path, body, event = '/api/draw/generate', {'description': '猫'}, 'web_generate'
        def upstream(**kwargs):
            calls.append(1); entered.set()
            assert release.wait(10)
            return _fake_result()
        monkeypatch.setattr('draw_portal.app.request_internal_draw', upstream)
        monkeypatch.setattr('draw_portal.app.materialize_generated_job', _fake_materialize)
        monkeypatch.setattr('draw_portal.app._notify_subscribe', lambda *args, **kwargs: False)
    else:
        client = _wx_client(monkeypatch, tmp_path, chat_limit=1)
        path, body, event = '/api/chat', {'message': '你好'}, 'wx_chat'
        def upstream(**kwargs):
            calls.append(1); entered.set()
            assert release.wait(10)
            return {'reply': '好', 'draw_intent': None}
        monkeypatch.setattr('draw_portal.chat.msg_sec_check', lambda **kwargs: None)
        monkeypatch.setattr('draw_portal.chat.request_internal_chat', upstream)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                    base_url='http://testserver', cookies=client.cookies) as http:
            first = asyncio.create_task(http.post(path, json=body))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                if cancel:
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                others = await asyncio.gather(*[http.post(path, json=body) for _ in range(7)])
                assert [r.status_code for r in others] == [429]*7
                assert len(calls) == 1
            finally:
                release.set()
            if not cancel:
                response = await first
                assert response.status_code == 200, response.text
                if kind == 'chat':
                    assert response.json()['remaining'] == 0
            await asyncio.gather(*list(quota._running))
            assert (await http.post(path, json=body)).status_code == 429

    with client:
        asyncio.run(run())
    with sqlite3.connect(tmp_path / 't.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM usage_events WHERE event_type=?', (event,)).fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0] == 0
