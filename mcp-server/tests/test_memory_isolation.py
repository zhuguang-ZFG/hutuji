"""记忆设备命名边界与召回预算回归。"""
import json
from types import SimpleNamespace

import pytest
from hutuji_mcp import memory_store as memory


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'settings', SimpleNamespace(output_dir=tmp_path))


@pytest.mark.parametrize('left,right', [('device:a', 'device_a'),
    ('aa:bb:cc:dd:ee:01', 'aa_bb_cc_dd_ee_01'), ('Device-A', 'device-a')])
def test_distinct_identifiers_do_not_share_windows_or_linux_paths(left, right):
    assert str(memory.memory_path_for(left)).lower() != str(memory.memory_path_for(right)).lower()
    memory.remember('偏好', '甲设备内容', left)
    assert json.loads(memory.recall(right))['memories'] == {}
    memory.forget('*', right)
    assert json.loads(memory.recall(left))['memories']['偏好'] == '甲设备内容'


def test_mac_case_uses_existing_canonical_bucket():
    device='aa:bb:cc:dd:ee:01'
    memory.remember('偏好','同一物理设备',device)
    assert json.loads(memory.recall(device.upper()))['memories']['偏好']=='同一物理设备'
    assert memory.memory_path_for(device).name == 'aa_bb_cc_dd_ee_01.json'


def test_filtered_recall_has_same_budget_as_full_recall():
    device='aa:bb:cc:dd:ee:01'
    for i in range(200):
        memory.remember(f'偏好{i:03d}', '值'*500, device)
    result=json.loads(memory.recall(device,'偏好'))
    assert len(result['memories']) <= memory.MEMORY_RECALL_ALL_LIMIT
    assert sum(len(k)+len(v) for k,v in result['memories'].items()) <= memory.MEMORY_RECALL_ALL_MAX_CHARS
    assert result['total'] == 200 and result['returned'] == len(result['memories'])
    assert '偏好199' in result['memories']
