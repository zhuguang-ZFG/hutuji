"""灰度只选择目标设备，旧目录兼容，策略错误拒绝而非全量放行。"""
import pytest
from draw_portal.firmware_catalog import recommended_for_board, plan_check

MAC='aa:bb:cc:dd:ee:ff'
def catalog(percent=0):
    return {'boards':{'board':{'version':'hutuji.1.0.0','rollout':{'id':'pilot-1','percent':percent,
        'devices':[MAC],'candidate':{'version':'hutuji.1.0.1','relpath':'s3/board/hutuji.1.0.1/xiaozhi.bin','sha256':'a'*64,'size':1234}}}}}

def test_allowlist_and_unlisted_device():
    c=catalog()
    assert recommended_for_board(c,'board',MAC)['version']=='hutuji.1.0.1'
    assert recommended_for_board(c,'board','11:22:33:44:55:66')['version']=='hutuji.1.0.0'
    assert recommended_for_board(c,'board')['version']=='hutuji.1.0.0'

@pytest.mark.parametrize('percent',[0,1,25,50,100])
def test_percentage_is_stable_and_monotonic(percent):
    c=catalog(percent)
    selected=[recommended_for_board(c,'board',f'02:00:00:00:00:{i:02x}')['version']=='hutuji.1.0.1' for i in range(100)]
    assert selected==[recommended_for_board(c,'board',f'02:00:00:00:00:{i:02x}')['version']=='hutuji.1.0.1' for i in range(100)]
    if percent==0:assert not any(selected)
    if percent==100:assert all(selected)
    prior=catalog(max(percent-1,0))
    assert all(not (recommended_for_board(prior,'board',f'02:00:00:00:00:{i:02x}')['version']=='hutuji.1.0.1') or selected[i] for i in range(100))

@pytest.mark.parametrize('bad',[None,{},True,101,-1,'50'])
def test_bad_percent_rejected(bad):
    c=catalog();c['boards']['board']['rollout']['percent']=bad
    with pytest.raises(ValueError):recommended_for_board(c,'board',MAC)

def test_old_catalog_and_plan():
    assert recommended_for_board({'boards':{'board':{'version':'hutuji.1.0.0'}}},'board',MAC)['version']=='hutuji.1.0.0'
    c=catalog();c['boards']['board']['version']='hutuji.1.0.0';c['boards']['board']['rollout']['candidate']['version']='hutuji.1.0.1'
    assert plan_check(board='board',current_version='hutuji.1.0.0',catalog=c,device_mac=MAC)['update_available']
    assert not plan_check(board='board',current_version='hutuji.1.0.0',catalog=c,device_mac='11:22:33:44:55:66')['update_available']

def test_bad_device_rejected():
    c=catalog();c['boards']['board']['rollout']['devices']=['bad']
    with pytest.raises(ValueError):recommended_for_board(c,'board',MAC)


@pytest.mark.parametrize('scenario', ['pilot', 'unlisted', 'busy', 'withdrawn', 'changed', 'invalid'])
def test_rollout_upgrade_uses_bound_target_and_rechecks_policy(tmp_path, monkeypatch, scenario):
    import copy
    import json
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store

    store = Store(tmp_path / 'rollout.db')
    target = '02:00:00:00:00:02' if scenario == 'unlisted' else MAC
    binding = SimpleNamespace(user_id='pilot-user', device_mac=target)
    first = catalog()
    current = copy.deepcopy(first)
    reads = []
    pushes = []

    def load(_path):
        reads.append(1)
        return copy.deepcopy(current)

    def status(**kwargs):
        assert kwargs['binding'].device_mac == target
        # check已取目录，发送前的第二次读取必须发现灰度撤回或目标变化。
        if scenario == 'withdrawn':
            current['boards']['board'].pop('rollout', None)
        elif scenario == 'changed':
            current['boards']['board']['rollout']['candidate'].update(
                version='hutuji.1.0.2', relpath='s3/board/hutuji.1.0.2/xiaozhi.bin')
        elif scenario == 'invalid':
            current['boards']['board']['rollout']['percent'] = 101
        return True, {'board': 'board', 'firmware_version': 'hutuji.1.0.0',
                      'state': 'streaming' if scenario == 'busy' else 'idle'}, ''

    def push(**kwargs):
        pushes.append(kwargs)
        return True, json.dumps({'ok': True})

    monkeypatch.setattr(ota, 'load_catalog', load)
    monkeypatch.setattr(ota, '_fetch_device_status', status)
    monkeypatch.setattr(ota, '_push_healed', push)
    result = ota.start_upgrade(store=store, binding=binding, catalog_path='catalog.json',
                               public_base='https://fw.example.test', signing_secret='',
                               api_base='unused', token_cipher_key=b'')
    if scenario == 'pilot':
        candidate = first['boards']['board']['rollout']['candidate']
        assert result['status'] == 'started'
        assert len(pushes) == 1
        assert pushes[0]['binding'].device_mac == target
        assert pushes[0]['arguments'] == {
            'url': 'https://fw.example.test/firmware/' + candidate['relpath'],
            'version': candidate['version'], 'board': 'board', 'sha256': candidate['sha256'],
        }
        assert store.get_firmware_ota_session(binding.user_id).target_version == candidate['version']
    else:
        assert not pushes
        assert result == {'status': 'error', 'detail':
                          'up_to_date' if scenario == 'unlisted' else
                          'busy' if scenario == 'busy' else 'check_failed'}
        assert not ota.is_upgrading(store, binding.user_id)
    assert len(reads) == (1 if scenario == 'unlisted' else 2)


@pytest.mark.parametrize('current,detail', [
    ('hutuji.1.0.0', None), ('hutuji.1.0.1', 'up_to_date'),
    ('hutuji.1.0.2', 'up_to_date'), ('', 'check_failed'),
    (None, 'check_failed'), ('broken', 'check_failed'), (True, 'check_failed'),
])
def test_upgrade_rechecks_second_device_version(tmp_path, monkeypatch, current, detail):
    import json
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store

    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps(catalog()), encoding='utf-8')
    store = Store(tmp_path / 'ota.db')
    binding = SimpleNamespace(user_id='u', device_mac=MAC)
    replies = iter(['hutuji.1.0.0', current])
    pushes = []
    monkeypatch.setattr(ota, '_fetch_device_status', lambda **kw: (True, {
        'board': 'board', 'state': 'idle', 'firmware_version': next(replies)}, ''))
    monkeypatch.setattr(ota, '_push_healed', lambda **kw: (pushes.append(kw) or True, '{"ok":true}'))
    result = ota.start_upgrade(store=store, binding=binding, catalog_path=str(path),
                               public_base='https://fw.example.test', signing_secret='',
                               api_base='', token_cipher_key=b'')
    if detail:
        assert result == {'status': 'error', 'detail': detail}
        assert not pushes
        assert not ota.is_upgrading(store, 'u')
        assert not store.get_firmware_ota_session('u').target_version
    else:
        assert result['status'] == 'started' and len(pushes) == 1
        assert pushes[0]['arguments']['version'] == 'hutuji.1.0.1'
