"""真实工位探测入口只读，错配不可记通过，令牌不进入证据。"""
import io,json
import pytest
from scripts.factory.inspect import inspect_device
from scripts.factory.core import FactoryError,BOARD,digest_json
from scripts.factory.store import Store
from test_package_flash import package

@pytest.mark.parametrize('wrong',[False,True])
def test_inspection_records_exact_pair(tmp_path,monkeypatch,wrong):
    recipe,path,manifest=package(tmp_path);store=Store(tmp_path/'db')
    store.register('SN1','B1',BOARD,digest_json(recipe),digest_json(manifest),'02:00:00:00:00:01','02:00:00:00:00:02','02:00:00:00:00:03')
    monkeypatch.setenv('HUTUJI_FACTORY_SESSION_TOKEN','private-token')
    data=dict(device_id='02:00:00:00:00:01',board=BOARD,firmware_version='hutuji.1.0.36',
              plotter_sku='nopaper',factory_paired=True,serial_number='SN1',
              paired_grbl_mac='02:00:00:00:00:09' if wrong else '02:00:00:00:00:02',
              connected=True,ready=True,authorized=True,grbl_settings_ok=True)
    class Response(io.BytesIO):status=200
    calls=[]
    def urlopen(req,**kwargs):calls.append(json.loads(req.data));return Response(json.dumps({'status':'ok','result':json.dumps(data)}).encode())
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    if wrong:
        with pytest.raises(FactoryError):inspect_device(store,'SN1',recipe,path,'qa',tmp_path/'evidence')
    else:inspect_device(store,'SN1',recipe,path,'qa',tmp_path/'evidence')
    assert calls==[{'device_mac':'02:00:00:00:00:01','tool':'hutuji.status'}]
    row=store.status('SN1')['checks']['pairing']
    assert row['result']==('fail' if wrong else 'pass')
    assert 'private-token' not in open(row['evidence'],encoding='utf-8').read()
