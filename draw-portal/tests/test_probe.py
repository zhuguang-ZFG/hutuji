"""绑定探活必须识别真实工具正文及目标设备，不能把HTTP200当成功。"""
import json
from io import BytesIO
import pytest
from draw_portal import probe

MAC='aa:bb:cc:dd:ee:01'

def response(monkeypatch, body):
    class Reply(BytesIO):
        status=200
    monkeypatch.setattr(probe,'urlopen',lambda *args, **kwargs: Reply(json.dumps(body).encode()))

@pytest.mark.parametrize('body',[{}, [], None, {'success':False,'data':{}},
    {'isError':True,'content':[{'text':'offline'}]},
    {'success':True,'data':{'isError':True,'content':[{'text':'offline'}]}},
    {'success':True,'data':'not a status'}, {'success':True,'data':{'ready':'false','state':'idle'}}])
def test_rejects_invalid_or_failed_success_response(monkeypatch,body):
    response(monkeypatch,body)
    assert probe.probe_device_token(api_base='https://example.test',device_token='private')[0] is False

@pytest.mark.parametrize('wrapper',[lambda s:s,lambda s:{'success':True,'data':s},
    lambda s:{'content':[{'text':json.dumps(s)}]}])
def test_accepts_offline_plotter_if_s3_itself_answers_for_expected_device(monkeypatch,wrapper):
    response(monkeypatch,wrapper({'device_id':MAC.upper(),'state':'idle','connected':False,'ready':False}))
    assert probe.probe_device_token(api_base='https://example.test',device_token='private',expected_device_mac=MAC)[0]

@pytest.mark.parametrize('device',[None,'aa:bb:cc:dd:ee:02','masked:01'])
def test_signed_binding_refuses_missing_or_wrong_device(monkeypatch,device):
    response(monkeypatch,{'success':True,'data':{'device_id':device,'state':'idle','connected':True,'ready':True}})
    assert not probe.probe_device_token(api_base='https://example.test',device_token='private',expected_device_mac=MAC)[0]
