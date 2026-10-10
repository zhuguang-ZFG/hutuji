"""发布包实际二进制布局与烧录驱动的错板/中断防线。"""
import hashlib
import json
from pathlib import Path
import struct
import pytest

from scripts.factory.core import BOARD,FactoryError,digest_json,validate_manifest
from scripts.factory.package import build
from scripts.factory.store import Store
from scripts.factory import hardware
from test_factory import recipe


def table(kind):
    values=[('nvs',1,2,0x9000,0x4000),('otadata',1,0,0xd000,0x2000),('ota_0',0,0x10,0x20000,0x3f0000),('assets',1,0x82,0x800000,0x800000)] if kind=='s3' else [('nvs',1,2,0x9000,0x5000),('otadata',1,0,0xe000,0x2000),('app0',0,0x10,0x10000,0x1e0000)]
    values += [('phy_init',1,1,0xf000,0x1000),('ota_1',0,0x11,0x410000,0x3f0000)] if kind=='s3' else [('app1',0,0x11,0x1f0000,0x1e0000),('spiffs',1,0x82,0x3d0000,0x30000)]
    raw=b''.join(struct.pack('<HBBII16sI',0x50aa,t,sub,offset,size,name.encode(),0) for name,t,sub,offset,size in values)
    return raw+b'\xff'*(0xc00-len(raw))


def firmware(kind,app=False):
    data=bytearray(256)
    if app and kind=='s3':
        struct.pack_into('<I',data,0,0xabcd5432)
        data[16:48]=b'hutuji.1.0.36\0'.ljust(32,b'\0')
        data[48:80]=b'xiaozhi\0'.ljust(32,b'\0')
        data[128:128+len(BOARD)+1]=BOARD.encode()+b'\0'
    header=bytearray(24);header[0:4]=bytes([0xe9,1,2,0x4f if kind=='s3' else 0x2f])
    header[12]=9 if kind=='s3' else 0;header[23]=1
    raw=header+struct.pack('<II',0x3c000020 if kind=='s3' else 0x3f400020,len(data))+data
    checksum=0xef
    for value in data:checksum^=value
    raw+=bytes(15-len(raw)%16)+bytes([checksum])
    return bytes(raw)+hashlib.sha256(raw).digest()


def package(tmp_path):
    r=recipe();rp=tmp_path/'recipe.json';rp.write_text(json.dumps(r),encoding='utf-8');items={}
    for kind,offsets in [('s3',[0,0x8000,0xd000,0x20000,0x800000]),('grbl',[0x1000,0x8000,0xe000,0x10000])]:
        segments=[]
        for i,offset in enumerate(offsets):
            path=tmp_path/f'{kind}-{i}.bin'
            data=table(kind) if offset==0x8000 else b'\xff'*256
            if offset==(0 if kind=='s3' else 0x1000):data=firmware(kind)
            if offset==(0x20000 if kind=='s3' else 0x10000):
                data=firmware(kind,app=True)
            path.write_bytes(data);segments.append({'offset':offset,'source':str(path)})
        items[kind]={'commit':'a'*40,'version':'hutuji.1.0.36','segments':segments}
    source=tmp_path/'inputs.json';source.write_text(json.dumps(items))
    build(rp,source,tmp_path/'package')
    path=tmp_path/'package/manifest.json';return r,path,validate_manifest(path,r)


def test_complete_package_and_bad_hash(tmp_path):
    r,path,manifest=package(tmp_path)
    file=path.parent/manifest['s3']['segments'][0]['path'];file.write_bytes(b'bad')
    with pytest.raises(FactoryError):validate_manifest(path,r)


def test_wrong_partition_even_with_matching_digest(tmp_path):
    r,path,m=package(tmp_path);seg=m['s3']['segments'][1];file=path.parent/seg['path']
    raw=bytearray(file.read_bytes());raw[12:16]=(0x5000).to_bytes(4,'little');file.write_bytes(raw);seg['sha256']=hashlib.sha256(raw).hexdigest();path.write_text(json.dumps(m))
    with pytest.raises(FactoryError):validate_manifest(path,r)


def test_wrong_rom_mac_never_writes(tmp_path,monkeypatch):
    r,path,m=package(tmp_path);store=Store(tmp_path/'line.db')
    store.register('SN1','B1',BOARD,digest_json(r),digest_json(m),'02:00:00:00:00:01','02:00:00:00:00:02','02:00:00:00:00:03')
    calls=[]
    def tool(args):calls.append(args);return 'MAC: 02:00:00:00:00:99'
    monkeypatch.setattr(hardware,'esptool',tool)
    with pytest.raises(FactoryError):hardware.flash(store,'SN1',r,path,'grbl','COM1','test',tmp_path/'evidence',Path('unused'),True)
    assert len(calls)==1 and calls[0][-1]=='read-mac'


def test_incomplete_backup_never_writes(tmp_path,monkeypatch):
    r,path,m=package(tmp_path);store=Store(tmp_path/'line.db')
    store.register('SN1','B1',BOARD,digest_json(r),digest_json(m),'02:00:00:00:00:01','02:00:00:00:00:02','02:00:00:00:00:03')
    calls=[]
    def tool(args):
        calls.append(args)
        if args[-1]=='read-mac':return 'MAC: 02:00:00:00:00:02'
        Path(args[-1]).write_bytes(b'\xff'*100);return ''
    monkeypatch.setattr(hardware,'esptool',tool)
    with pytest.raises(FactoryError):hardware.flash(store,'SN1',r,path,'grbl','COM1','test',tmp_path/'evidence',Path('unused'),True)
    assert not any('write-flash' in a for a in calls)


@pytest.mark.parametrize('fail_verify',[False,True])
def test_grbl_first_flash_verify_and_no_repeat(tmp_path,monkeypatch,fail_verify):
    r,path,m=package(tmp_path);store=Store(tmp_path/'line.db')
    store.register('SN1','B1',BOARD,digest_json(r),digest_json(m),'02:00:00:00:00:01','02:00:00:00:00:02','02:00:00:00:00:03')
    calls=[]
    def tool(args):
        calls.append(args)
        if args[-1]=='read-mac':return 'MAC: 02:00:00:00:00:02'
        if 'read-flash' in args:Path(args[-1]).write_bytes(b'\xff'*0x400000)
        if 'verify-flash' in args and fail_verify:raise FactoryError('模拟验写失败')
        return ''
    monkeypatch.setattr(hardware,'esptool',tool)
    if fail_verify:
        with pytest.raises(FactoryError):hardware.flash(store,'SN1',r,path,'grbl','COM1','test',tmp_path/'evidence',Path('unused'),True)
    else:hardware.flash(store,'SN1',r,path,'grbl','COM1','test',tmp_path/'evidence',Path('unused'),True)
    assert sum('write-flash' in args for args in calls)==1
    assert store.status('SN1')['checks']['flash_grbl']['result']==('fail' if fail_verify else 'pass')
    with pytest.raises(FactoryError):hardware.flash(store,'SN1',r,path,'grbl','COM1','test',tmp_path/'evidence',Path('unused'),True)
    assert sum('write-flash' in args for args in calls)==1
