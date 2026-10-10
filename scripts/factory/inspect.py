"""通过既有鉴权Portal只读核对整机；输出只含白名单身份和状态，不落token。"""
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request

from .core import FactoryError, BOARD, mac, digest_json, validate_manifest


def inspect_device(store,sn,recipe,manifest_path,operator,directory):
    unit=store.unit(sn);manifest=validate_manifest(manifest_path,recipe)
    if digest_json(manifest)!=unit['manifest_sha'] or digest_json(recipe)!=unit['recipe_sha']:
        raise FactoryError('不是该台冻结发布包或配方')
    base=os.environ.get('HUTUJI_FACTORY_PORTAL_URL','https://hutuji.donglicao.com/draw-upload').rstrip('/')
    parsed=urllib.parse.urlsplit(base)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FactoryError('工位Portal必须为无凭据HTTPS地址')
    token=os.environ.get('HUTUJI_FACTORY_SESSION_TOKEN','')
    if not token:raise FactoryError('缺少工位账号会话；先用正常用户绑定流程登录')
    request=urllib.request.Request(base+'/api/device/control',data=json.dumps({'device_mac':unit['macs']['s3'],'tool':'hutuji.status'}).encode(),
        headers={'x-session':token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=25) as response:
        raw=response.read(65537)
        if response.status!=200 or len(raw)>65536:raise FactoryError('设备状态响应异常')
    outer=json.loads(raw)
    if outer.get('status')!='ok':raise FactoryError('设备状态未成功；请检查登录、绑定或网络')
    data=outer.get('result')
    if isinstance(data,str):data=json.loads(data)
    if not isinstance(data,dict):raise FactoryError('设备状态正文无效')
    identity=(data.get('device_id')==unit['macs']['s3'] and data.get('board')==BOARD and
              data.get('firmware_version')==manifest['s3']['version'] and data.get('plotter_sku')=='nopaper')
    paired=(identity and data.get('factory_paired') is True and data.get('serial_number')==sn and
            data.get('paired_grbl_mac','').lower()==unit['macs']['grbl'])
    ready=paired and all(data.get(k) is True for k in ('connected','ready','authorized','grbl_settings_ok'))
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(sn+f'-g{unit["generation"]}-inspect-{time.time_ns()}.json')
    keys=('device_id','board','firmware_version','plotter_sku','factory_paired','serial_number','paired_grbl_mac',
          'connected','ready','authorized','grbl_settings_ok','state','grbl_state','mpos_x','mpos_y','mpos_z')
    evidence={'sn':sn,'status':{k:data.get(k) for k in keys}}
    path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    store.event(sn,'pairing','pass' if paired else 'fail',operator,path,evidence)
    store.event(sn,'authorization','pass' if ready else 'fail',operator,path,evidence)
    if not ready:raise FactoryError('整机版本、配对或授权不符，已记录失败')
    return evidence
