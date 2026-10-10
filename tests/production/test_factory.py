"""产线门禁和档案回归，不需要实机或云凭据。"""
import copy
import json
from pathlib import Path
import pytest
from scripts.factory.core import BOARD,CHECKS,FactoryError,validate_recipe,mac,digest_json,compare_settings,validate_manifest,validate_measurements
from scripts.factory.store import Store
from scripts.factory.documents import validate_delivery,test_artifacts as generate_artifacts

ROOT=Path(__file__).resolve().parents[2]
def recipe():return json.loads((ROOT/'production/nopaper-freenove-v1.json').read_text(encoding='utf-8'))
def unit(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    store.register('SN001','B001',BOARD,digest_json(recipe()),'manifest','02:00:00:00:00:01','02:00:00:00:00:02','02:00:00:00:00:03')
    return store

def delivery(tmp_path):
    p=tmp_path/'report.txt';p.write_text('test report')
    review={'reviewer':'tester','date':'2026-10-10','report':str(p)}
    return dict(model='test',seller='test',support='test',warranty='test',privacy_url='https://example.test/privacy',source_url='https://example.test/source',license_review=review,electrical_review=review,radio_review=review)

def measurement():return {'hours':4,'jobs':30,'errors':0,'max_error_mm':1,'missed_strokes':0,'lost_steps':0}

@pytest.mark.parametrize('value',['bad','00:00:00:00:00:00','ff:ff:ff:ff:ff:ff','01:00:00:00:00:00'])
def test_bad_mac(value):
    with pytest.raises(FactoryError):mac(value)

def test_recipe_known_sample():
    value=recipe();validate_recipe(value)
    assert value['settings']=={'110':6000,'111':6000,'112':1200,'120':3000,'121':3000,'122':3000}
    assert not compare_settings({**value['settings'],**value['readonly']},value)

@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),0,20000])
def test_recipe_invalid_values(value):
    data=recipe();data['settings']['110']=value
    with pytest.raises(FactoryError):validate_recipe(data)

def test_duplicate_identity_and_transaction(tmp_path):
    store=unit(tmp_path)
    with pytest.raises(FactoryError):store.register('SN002','B001',BOARD,'r','m','02:00:00:00:00:04','02:00:00:00:00:02','02:00:00:00:00:05')
    with pytest.raises(FactoryError):store.unit('SN002')

def test_missing_evidence_blocks_release(tmp_path):
    store=unit(tmp_path)
    assert len(store.status('SN001')['missing'])==len(CHECKS)
    with pytest.raises(FactoryError):store.release('SN001','operator',recipe(),delivery(tmp_path))

def test_fail_supersedes_pass_and_rework_requires_all(tmp_path):
    store=unit(tmp_path);evidence=tmp_path/'evidence.json';evidence.write_text('{}')
    for name in CHECKS:store.event('SN001',name,'pass','operator',evidence,measurement())
    store.event('SN001','paper','fail','operator',evidence,{})
    assert store.status('SN001')['missing']==['paper']
    store.event('SN001','paper','pass','operator',evidence,measurement())
    store.release('SN001','qa',recipe(),delivery(tmp_path))
    with pytest.raises(FactoryError):store.event('SN001','paper','pass','operator',evidence,{})
    store.rework('SN001','repair','换笔夹')
    assert len(store.status('SN001')['missing'])==len(CHECKS)

def test_evidence_tamper_and_backup(tmp_path):
    store=unit(tmp_path);evidence=tmp_path/'e.txt';evidence.write_text('before')
    store.event('SN001','paper','pass','operator',evidence,{})
    evidence.write_text('after')
    assert 'paper:evidence' not in store.status('SN001')['missing']
    snapshot=Path(store.status('SN001')['checks']['paper']['evidence']);snapshot.write_text('tampered')
    assert 'paper:evidence' in store.status('SN001')['missing']
    store.backup(tmp_path/'backup.sqlite')
    assert Store(tmp_path/'backup.sqlite').unit('SN001')['sn']=='SN001'
    with pytest.raises(FactoryError):store.backup(tmp_path/'backup.sqlite')


def test_archive_preserves_pending_and_final_evidence(tmp_path):
    store=unit(tmp_path);evidence=tmp_path/'event.json';evidence.write_text('pending')
    store.event('SN001','paper','pending','qa',evidence,{})
    evidence.write_text('final');store.event('SN001','paper','pass','qa',evidence,{})
    store.archive(tmp_path/'archive')
    restored=Store(tmp_path/'archive/line.db').history('SN001')
    assert [Path(r['evidence']).read_text() for r in restored['history']]==['pending','final']
    import shutil
    shutil.copytree(tmp_path/'archive',tmp_path/'moved')
    assert 'paper:evidence' not in Store(tmp_path/'moved/line.db').status('SN001')['missing']

def test_delivery_refuses_placeholders():
    with pytest.raises(FactoryError):validate_delivery({'model':'待定'})

def test_artifacts_are_real_valid_gcode(tmp_path):
    result=generate_artifacts(tmp_path)
    code=Path(result['gcode']).read_text()
    assert 'M30' not in code and 'G92' not in code and 'X30.000' in code


@pytest.mark.parametrize('bad',[None,True,-1,float('nan'),float('inf'),'0'])
def test_bad_measurement_never_passes(bad):
    value=measurement();value['max_error_mm']=bad
    checks={k:{'detail':json.dumps(value)} for k in ('aging','paper')}
    with pytest.raises(FactoryError):validate_measurements(checks,recipe())

def test_operation_blocks_other_station_and_rework(tmp_path):
    store=unit(tmp_path)
    with store.operation('SN001','COM1','a'):
        with pytest.raises(FactoryError):
            with Store(store.path).operation('SN001','COM2','b'):pass
        with pytest.raises(FactoryError):store.rework('SN001','b','repair')
    store.rework('SN001','a','repair')

def test_real_nvs_generator_and_identity(tmp_path):
    from scripts.factory.hardware import nvs_image,verify_factory_nvs
    import os
    generator=Path(os.environ.get('IDF_PATH','C:/Users/zhugu/.espressif/v6.0.1/v6.0.1/esp-idf'))/'components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py'
    if not generator.exists():pytest.skip('需要真实IDF NVS生成器')
    record=unit(tmp_path).unit('SN001');dest=tmp_path/'factory.bin'
    nvs_image(record,dest,generator)
    verify_factory_nvs(dest.read_bytes(),record,generator)
    wrong=copy.deepcopy(record);wrong['sn']='SN002'
    with pytest.raises(FactoryError):verify_factory_nvs(dest.read_bytes(),wrong,generator)
    raw=bytearray(dest.read_bytes());raw[100]^=1
    with pytest.raises(FactoryError):verify_factory_nvs(raw,record,generator)
