"""检验代次和烧录备份必须能随档案完整搬到售后工位。"""
import json
from pathlib import Path
import shutil

import pytest

from scripts.factory import hardware
from scripts.factory.core import BOARD, FactoryError, digest_json
from scripts.factory.store import Store
from test_factory import unit
from test_package_flash import package


def test_measurement_copy_cannot_join_new_rework_generation(tmp_path, monkeypatch):
    store = unit(tmp_path)
    evidence = tmp_path / 'paper.txt'
    evidence.write_text('old measurement')
    original = shutil.copyfile
    attempts = []

    def copy_and_rework(source, target, *args, **kwargs):
        result = original(source, target, *args, **kwargs)
        try:
            Store(store.path).rework('SN001', 'repair', 'new inspection')
        except FactoryError:
            attempts.append('blocked')
        else:
            attempts.append('reworked')
        return result

    monkeypatch.setattr(shutil, 'copyfile', copy_and_rework)
    store.event('SN001', 'paper', 'pass', 'qa', evidence, {})
    status = store.status('SN001')
    assert attempts == ['blocked']
    assert status['generation'] == status['checks']['paper']['generation'] == 1


def test_resolved_executor_cannot_write_into_new_generation(tmp_path):
    store = unit(tmp_path)
    evidence = tmp_path / 'stopped.txt'
    evidence.write_text('operator confirmed stopped')
    with store.operation('SN001', 'COM1', 'old'):
        other = Store(store.path)
        other.resolve_operation('SN001', 'repair', evidence)
        other.rework('SN001', 'repair', 'restart inspection')
        with pytest.raises(FactoryError):
            store.event('SN001', 'paper', 'pass', 'old', evidence, {})
    assert 'paper' in store.status('SN001')['missing']


@pytest.mark.parametrize('fail_verify', [False, True])
def test_flash_archive_contains_restorable_backup_after_success_or_failure(tmp_path, monkeypatch, fail_verify):
    recipe, manifest_path, manifest = package(tmp_path)
    store = Store(tmp_path / 'line.db')
    store.register('SN1', 'B1', BOARD, digest_json(recipe), digest_json(manifest),
                   '02:00:00:00:00:01', '02:00:00:00:00:02', '02:00:00:00:00:03')
    expected = b'\xff' * 0x400000

    def tool(args):
        if args[-1] == 'read-mac':
            return 'MAC: 02:00:00:00:00:02'
        if 'read-flash' in args:
            Path(args[-1]).write_bytes(expected)
        if 'verify-flash' in args and fail_verify:
            raise FactoryError('模拟验写失败')
        return ''

    monkeypatch.setattr(hardware, 'esptool', tool)
    args = (store, 'SN1', recipe, manifest_path, 'grbl', 'COM1', 'qa', tmp_path / 'raw', Path('unused'), True)
    if fail_verify:
        with pytest.raises(FactoryError):
            hardware.flash(*args)
    else:
        hardware.flash(*args)
    original = store.status('SN1')['checks']['flash_grbl']
    assert original['result'] == ('fail' if fail_verify else 'pass')
    assert original.get('attachments'), '烧录备份必须成为台账附件，而非只有正文绝对路径'
    store.archive(tmp_path / 'archive')
    shutil.copytree(tmp_path / 'archive', tmp_path / 'moved')
    restored = Store(tmp_path / 'moved/line.db').history('SN1')
    final = restored['checks']['flash_grbl']
    backup = next(item for item in final['attachments'] if item['name'] == 'flash_backup')
    assert Path(backup['path']).is_relative_to(tmp_path / 'moved')
    assert Path(backup['path']).read_bytes() == expected
    detail = json.loads(final['detail'])
    assert detail['backup_complete'] is True
    assert detail['write_attempted'] is True


def test_modified_attachment_blocks_status_and_archive(tmp_path):
    store = unit(tmp_path)
    evidence = tmp_path / 'flash.json'
    evidence.write_text('{}')
    backup = tmp_path / 'backup.bin'
    backup.write_bytes(b'original backup')
    store.event('SN001', 'flash_s3', 'pass', 'qa', evidence, {}, attachments={'flash_backup': backup})
    item = store.status('SN001')['checks']['flash_s3']['attachments'][0]
    Path(item['path']).write_bytes(b'changed backup')
    assert 'flash_s3:attachments' in store.status('SN001')['missing']
    with pytest.raises(FactoryError):
        store.archive(tmp_path / 'archive')


@pytest.mark.parametrize('damage', ['content', 'size', 'missing', 'missing_record'])
def test_released_report_integrity_in_status_and_batch(tmp_path, monkeypatch, damage):
    import csv
    import sys
    from scripts.factory.__main__ import main
    from scripts.factory.core import CHECKS
    from test_factory import recipe, delivery, measurement

    store = unit(tmp_path)
    evidence = tmp_path / 'check.json'
    evidence.write_text('{}')
    for name in CHECKS:
        store.event('SN001', name, 'pass', 'qa', evidence, measurement(), attachments={'flash_backup':evidence} if name.startswith('flash_') else None)
    store.release('SN001', 'qa', recipe(), delivery(tmp_path))
    assert store.status('SN001')['missing'] == []
    # 归档搬迁后也检查本地相对路径，不依赖原始报告。
    store.archive(tmp_path / 'archive')
    shutil.copytree(tmp_path / 'archive', tmp_path / 'moved')
    store = Store(tmp_path / 'moved/line.db')
    released = store.status('SN001')['checks']['release']
    attachment = released['attachments'][0]
    path = Path(attachment['path'])
    if damage == 'content': path.write_bytes(b'x' * path.stat().st_size)
    elif damage == 'size': path.write_bytes(b'x')
    elif damage == 'missing': path.unlink()
    else:
        with store.connect() as db:
            db.execute('DELETE FROM event_attachments WHERE event_id=? AND name=?',
                       (released['id'], attachment['name']))
    status = store.status('SN001')
    assert status['released'] == 1
    assert 'release:attachments' in status['missing']
    target = tmp_path / 'batch.csv'
    monkeypatch.setattr(sys, 'argv', ['factory', '--db', str(store.path), 'batch',
                                    '--batch', 'B001', '--output', str(target)])
    main()
    with target.open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['released'] == '1' and 'release:attachments' in row['missing']
    store.rework('SN001', 'repair', '重新验收')
    assert 'release:attachments' not in store.status('SN001')['missing']
