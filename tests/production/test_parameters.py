"""参数执行器必须逐轴写入、完整读回，错误不得记为合格。"""
import copy
from pathlib import Path
import pytest
from scripts.factory import hardware
from scripts.factory.core import BOARD,FactoryError,digest_json
from scripts.factory.store import Store
from test_factory import recipe

@pytest.mark.parametrize('wrong_readback',[False,True])
def test_parameters_readback_and_single_attempt(tmp_path,monkeypatch,wrong_readback):
    r=recipe();store=Store(tmp_path/'db')
    store.register('SN1','B1',BOARD,digest_json(r),'m','02:00:00:00:00:01','02:00:00:00:00:02','02:00:00:00:00:03')
    actual={**r['settings'],**r['readonly'],'130':200.0};actual['110']=10000
    calls=[]
    class Wire:
        def __init__(self,port):assert port=='COM1'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def snapshot(self,expected):
            assert expected=='02:00:00:00:00:02'
            return actual.copy()
        def query(self,line):
            calls.append(line)
            if not wrong_readback:actual['110']=6000
            return 'ok'
    monkeypatch.setattr(hardware,'Grbl',Wire)
    if wrong_readback:
        with pytest.raises(FactoryError):hardware.apply_parameters(store,'SN1',r,'COM1','qa',tmp_path/'evidence',True)
    else:hardware.apply_parameters(store,'SN1',r,'COM1','qa',tmp_path/'evidence',True)
    assert calls==['$110=6000'] and actual['130']==200.0
    assert store.status('SN1')['checks']['parameters']['result']==('fail' if wrong_readback else 'pass')
    with pytest.raises(FactoryError):hardware.apply_parameters(store,'SN1',r,'COM1','qa',tmp_path/'evidence',True)
    assert len(calls)==1


@pytest.mark.parametrize('identity,accepted', [
    ('[MSG:Mode=STA:SSID=工位:Status=Connected:IP=192.168.1.8:MAC=02-00-00-00-00-02]', True),
    ('[MSG:Mode=STA:SSID=home:Status=Not connected:IP=0.0.0.0:MAC=02-00-00-00-00-02]', True),
    ('[MSG:Mode=STA:SSID=MAC=02-00-00-00-00-02:Status=Connected:IP=192.168.1.8:MAC=02-00-00-00-00-09]', False),
    ('[MSG:Mode=AP:SSDI=GRBL_ESP:IP=192.168.0.1:MAC=02-00-00-00-00-02]', False),
    ('[MSG:Mode=STA:SSID=home:Status=Connected:IP=192.168.1.8:MAC=02-00-00-00-00-020]', False),
    ('[MSG:Mode=STA:SSID=home:Status=Connected:IP=192.168.1.8:MAC=02-00-00-00-00-02', False),
    ('[MSG:MAC=02-00-00-00-00-02]', False),
    ('[MSG:Mode=STA:SSID=home:Status=Connected:IP=192.168.1.8:MAC=02-00-00-00-00-02]\n'
     '[MSG:Mode=STA:SSID=home:Status=Connected:IP=192.168.1.9:MAC=02-00-00-00-00-09]', False),
])
def test_parameter_snapshot_uses_actual_sta_identity(identity, accepted):
    # 经过真实snapshot解析，只替换串口收发，防SSID中的文字被当成本台身份。
    wire = object.__new__(hardware.Grbl)
    queries = []
    values = {**recipe()['settings'], **recipe()['readonly'], **{str(n): 1 for n in range(30, 50)}}

    def query(command):
        queries.append(command)
        return {
            '$I': '[VER:1.3a.20260910:]\n' + identity + '\nok',
            '?': '<Idle|MPos:0.000,0.000,0.000|Bf:249,128>',
            '$$': '\n'.join(f'${key}={value}' for key, value in values.items()) + '\nok',
        }[command]

    wire.query = query
    if accepted:
        assert wire.snapshot('02:00:00:00:00:02') == values
        assert queries == ['$I', '?', '$$']
    else:
        with pytest.raises(FactoryError):
            wire.snapshot('02:00:00:00:00:02')
        assert queries == ['$I']
