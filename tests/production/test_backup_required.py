"""删除备份登记须阻断状态和归档，旧代次失败后的备份也必须保留。"""
import pytest
from scripts.factory.core import FactoryError
from test_factory import unit


@pytest.mark.parametrize('kind',['s3','grbl'])
@pytest.mark.parametrize('result,detail',[('pass',{}),('fail',{'backup_complete':True}),('pending',{'write_attempted':True})])
@pytest.mark.parametrize('rework',[False,True])
def test_deleted_backup_registration_blocks_archive(tmp_path,kind,result,detail,rework):
    store=unit(tmp_path)
    evidence=tmp_path/'event.json';evidence.write_text('{}')
    backup=tmp_path/'backup.bin';backup.write_bytes(b'backup-test')
    name='flash_'+kind
    store.event('SN001',name,result,'qa',evidence,detail,attachments={'flash_backup':backup})
    event=store.status('SN001')['checks'][name]
    with store.connect() as db:db.execute('DELETE FROM event_attachments WHERE event_id=?',(event['id'],))
    if result=='pass':assert name+':flash_backup' in store.status('SN001')['missing']
    if rework:store.rework('SN001','qa','repair')
    with pytest.raises(FactoryError,match='必需附件'):
        store.archive(tmp_path/'bundle')


@pytest.mark.parametrize('detail', ['{', 'null', '[]', 'true'])
def test_corrupt_event_detail_blocks_status_and_archive(tmp_path, detail):
    store = unit(tmp_path)
    evidence = tmp_path / 'event.json'
    evidence.write_text('{}')
    backup = tmp_path / 'backup.bin'
    backup.write_bytes(b'backup-test')
    store.event('SN001', 'flash_s3', 'pass', 'qa', evidence, {},
                attachments={'flash_backup': backup})
    event = store.status('SN001')['checks']['flash_s3']
    with store.connect() as db:
        db.execute('UPDATE events SET detail=? WHERE id=?', (detail, event['id']))
    assert 'flash_s3:flash_backup' in store.status('SN001')['missing']
    with pytest.raises(FactoryError, match='必需附件'):
        store.archive(tmp_path / 'bundle')
