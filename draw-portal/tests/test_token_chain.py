"""纸张查询与发送连续自愈时，CAS必须使用实际发送的凭据快照。"""
from types import SimpleNamespace

import pytest

from draw_portal.crypto import decrypt_token
from hutuji_mcp.paper_config import PaperConfig
from test_paper_settings import _code, _status
from test_voice_api import _bound_voice_client


def _transport(monkeypatch, key):
    from draw_portal import push

    status_calls = []
    draw_calls = []
    minted = iter(('paper-token', 'draw-token'))

    def status(**kw):
        status_calls.append(kw['device_token'])
        if kw['device_token'] == 'old-token':
            return False, 'HTTP 401'
        return True, _status(PaperConfig.default_for('nopaper'))

    def draw(**kw):
        draw_calls.append(kw['device_token'])
        return (True, 'previewing') if kw['device_token'] == 'draw-token' else (False, 'HTTP 401')

    monkeypatch.setattr('draw_portal.app.push_tool_call', status)
    monkeypatch.setattr(push, 'mint_messaging_token', lambda **kw: next(minted))
    monkeypatch.setattr(push, 'push_draw_to_device', draw)
    monkeypatch.setattr('hutuji_mcp.device_messaging.save_device_token', lambda _: None)
    return status_calls, draw_calls


def test_internal_push_uses_post_status_credential(tmp_path, monkeypatch):
    monkeypatch.setenv('DRAW_PORTAL_USAGE_INGEST_SECRET', 'paper-internal-test')
    client, store, user, key = _bound_voice_client(monkeypatch, tmp_path)
    monkeypatch.setattr('draw_portal.app._is_loopback_client', lambda _: True)
    headers = {'x-hutuji-usage-secret': 'paper-internal-test'}
    monkeypatch.setattr('draw_portal.app.push_tool_call',
                        lambda **kw: (True, _status(PaperConfig.default_for('nopaper'))))
    target = client.get('/api/internal/paper', params={'device_id': 'aa:bb:cc:dd:ee:ff'},
                        headers=headers).json()
    # 产物校验在既有纸张测试覆盖；这里仅隔离外部能力URL下载。
    monkeypatch.setattr('draw_portal.paper_settings.verify_capabilities', lambda *args: None)
    _, draw_calls = _transport(monkeypatch, key)
    response = client.post('/api/internal/paper/push', headers=headers, json={
        'target': target, 'pages': [{'url': 'https://example.test/a.gcode',
                                   'preview_url': 'https://example.test/a.png'}],
    })
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'sent'
    assert draw_calls == ['paper-token', 'draw-token']
    assert decrypt_token(key, store.get_binding_for_user(user.id).device_token_enc) == 'draw-token'


@pytest.mark.parametrize('kind', ['draw', 'write'])
def test_voice_push_uses_post_status_credential(tmp_path, monkeypatch, kind):
    from draw_portal import voice_jobs

    monkeypatch.setenv('DRAW_PORTAL_USAGE_INGEST_SECRET', 'voice-test')
    monkeypatch.setenv('DRAW_PORTAL_GFONT_DIR', str(tmp_path / 'fonts'))
    client, store, user, key = _bound_voice_client(monkeypatch, tmp_path)
    _, draw_calls = _transport(monkeypatch, key)
    published = SimpleNamespace(basename='test', gcode_url='https://example.test/a.gcode',
                                preview_url='https://example.test/a.png')

    def materialize(*, job_dir, **kw):
        (job_dir / 'draw.gcode').write_bytes(_code())

    def write(text, job_dir, **kw):
        materialize(job_dir=job_dir)
        return SimpleNamespace(page_count=1)

    monkeypatch.setattr(voice_jobs, 'request_internal_draw', lambda **kw: object())
    monkeypatch.setattr(voice_jobs, 'materialize_generated_job', materialize)
    monkeypatch.setattr(voice_jobs, 'publish_job_files', lambda **kw: published)
    monkeypatch.setattr(voice_jobs, 'process_write_text', write)
    monkeypatch.setattr(voice_jobs, 'publish_doc_pages', lambda **kw: [published])

    def chat(**kw):
        reply, preview = kw['run_portal_tool']('voice.' + kind, {'description': '猫', 'text': '猫'})
        return {'reply': reply, 'tool_traces': [], 'preview': preview}

    monkeypatch.setattr('draw_portal.voice_agent.chat', chat)
    response = client.post('/api/voice/turn', json={'session_id': 'heal-chain', 'text': '画猫'})
    assert response.status_code == 200, response.text
    assert response.json()['preview']['kind'] == kind
    assert draw_calls == ['paper-token', 'draw-token']
    assert decrypt_token(key, store.get_binding_for_user(user.id).device_token_enc) == 'draw-token'
