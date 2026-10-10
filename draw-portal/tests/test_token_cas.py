"""真实SQLite及控制路由交错：旧自愈不能覆盖新凭据或再次下发动作。"""
from dataclasses import replace
import os
import pytest
from draw_portal.crypto import encrypt_token
from draw_portal.store import Store
from test_paper_settings import _setup
from test_wx_control import _clients, _announce


def test_only_one_writer_can_replace_the_same_token(tmp_path):
    store, cfg, binding = _setup(tmp_path)
    assert store.update_binding_token_if_current(binding, 'new-a')
    assert not Store(tmp_path/'paper.db').update_binding_token_if_current(binding, 'late-b')
    assert store.get_binding_for_mac(binding.device_mac).device_token_enc == 'new-a'
    current = replace(binding, device_token_enc='new-a')
    assert store.update_binding_token_if_current(current, 'new-c')


@pytest.mark.parametrize('change', ['token', 'generation', 'owner'])
def test_control_does_not_retry_after_mint_interleaving(tmp_path, monkeypatch, change):
    from draw_portal import app as mod
    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, 'AAAAAA')
    assert client.post('/api/user/bind/complete', json={'bind_code':'AAAAAA'}).status_code == 200
    store = Store(tmp_path/'t.db')
    binding = store.get_binding_for_mac('aa:bb:cc:dd:ee:ff')
    calls=[]
    monkeypatch.setattr(mod,'push_tool_call',lambda **kw:(calls.append(kw['name']) or False,'HTTP 401'))
    key=os.environ['DRAW_PORTAL_TOKEN_CIPHER_KEY'].encode()
    winner=encrypt_token(key,'newer-token')
    def mint(**kw):
        if change=='token':
            assert store.update_binding_token_if_current(binding,winner)
        else:
            owner = binding.user_id if change=='generation' else store.create_wx_user('replacement').id
            store.delete_binding_for_mac(binding.device_mac,user_id=binding.user_id)
            store.set_device_binding(user_id=owner,device_mac=binding.device_mac,device_token_enc=winner,
                                     bound_at=binding.bound_at+1,token_probe_ok=True,machine_type='nopaper')
        return 'late-old-request-token'
    monkeypatch.setattr('draw_portal.push.mint_messaging_token',mint)
    response=client.post('/api/device/control',json={'tool':'hutuji.pen_test'})
    assert response.status_code==409
    assert calls==['hutuji.pen_test']
    assert store.get_binding_for_mac(binding.device_mac).device_token_enc==winner
