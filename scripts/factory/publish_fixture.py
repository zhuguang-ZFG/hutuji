"""在已配置服务环境生成本台纸张快照绑定的固定测试件；不下发或确认设备。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .core import FactoryError,mac
from .documents import test_artifacts


def publish(device_mac,receipt):
    root=Path(__file__).resolve().parents[2]
    sys.path[:0]=[str(root/'draw-portal'),str(root/'mcp-server')]
    from draw_portal.config import load_config
    from draw_portal.store import Store
    from draw_portal.paper_settings import read_settings,check_owner
    from draw_portal.push import push_tool_call,publish_job_files
    from hutuji_mcp.paper_artifacts import write_paper_metadata
    from hutuji_mcp.gcode_gen import validate_gcode
    from PIL import Image,ImageDraw
    config=load_config();store=Store(config.db_path)
    binding=store.get_binding_for_mac(mac(device_mac))
    if not binding:raise FactoryError('测试件必须属于已绑定的本台设备')
    snapshot=read_settings(store,config,binding,require_live=True,require_active=True,call_tool=push_tool_call)
    if snapshot.machine!='nopaper':raise FactoryError('只为量产nopaper生成固定测试件')
    if receipt.exists():raise FactoryError('能力回执目标已存在')
    with tempfile.TemporaryDirectory(prefix='factory-fixture-') as temporary:
        job=Path(temporary);test_artifacts(job)
        path=job/'draw.gcode';path.write_bytes((job/'factory-20mm.gcode').read_bytes())
        raw=path.read_bytes();validate_gcode(raw.decode(),total_bytes=len(raw),profile=snapshot.paper.to_profile())
        write_paper_metadata(path,snapshot.paper,data=raw)
        image=Image.new('RGB',(240,240),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((60,60,180,180),outline='black',width=2)
        draw.line((60,120,180,120),fill='black',width=2);draw.line((120,60,120,180),fill='black',width=2)
        image.save(job/'preview.png')
        check_owner(store,binding,require_active=True)
        result=publish_job_files(job_dir=job,mcp_output_dir=config.mcp_output_dir,public_base_url=config.public_base_url)
    receipt.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(receipt,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as output:
        json.dump({'device_mac':device_mac,'gcode_url':result.gcode_url,'preview_url':result.preview_url,
                   'paper_marker':snapshot.paper.marker,'measured_square_mm':20,'sent':False},output)
    return {'receipt':str(receipt),'sent':False,'next':'按原设备预览/确认流程使用该回执，禁止跨设备复用'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='在服务环境发布固定20mm测试件，不发送设备')
    parser.add_argument('--device-mac',required=True);parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(publish(args.device_mac,args.receipt),ensure_ascii=False))
