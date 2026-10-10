"""显式端口、身份和单次执行意图；不扫串口、不复制NVS、不自动复位运动机。"""
from __future__ import annotations
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .core import FactoryError, BOARD, compare_settings, digest, digest_json, validate_manifest, mac


def verify_factory_nvs(raw,unit,generator):
    """用配套IDF解析器按namespace与CRC读取有效项，不能搜残留字符串判身份。"""
    import importlib.util
    from zlib import crc32
    parser=generator.parent.parent/'nvs_partition_tool/nvs_parser.py'
    if not parser.is_file():raise FactoryError('缺少配套IDF NVS解析器')
    spec=importlib.util.spec_from_file_location('factory_nvs_parser',parser)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    partition=module.NVS_Partition('factory',bytearray(raw))
    entries=[]
    for page in sorted(partition.pages,key=lambda p:p.header['page_index']):
        if page.is_empty:continue
        if page.header['status'] not in ('Active','Full') or page.header['crc']['original']!=page.header['crc']['computed']:
            raise FactoryError('NVS页状态或CRC不符')
        entries.extend(e for e in page.entries if e.state=='Written')
    namespaces=set()
    for entry in entries:
        if entry.metadata['namespace']==0 and entry.key=='hutuji_factory' and entry.metadata['type']=='uint8_t':
            if entry.metadata['crc']['original']!=entry.metadata['crc']['computed']:raise FactoryError('NVS namespace CRC不符')
            namespaces.add(entry.data['value'])
    if len(namespaces)!=1:raise FactoryError('工厂NVS缺失或歧义')
    values={}
    for entry in entries:
        if entry.metadata['namespace'] not in namespaces:continue
        if entry.metadata['crc']['original']!=entry.metadata['crc']['computed']:raise FactoryError('工厂NVS字段CRC不符')
        if entry.metadata['type']=='uint8_t':value=entry.data['value']
        elif entry.metadata['type']=='string':
            content=b''.join(bytes(child.raw) for child in entry.children)[:entry.data['size']]
            if not content.endswith(b'\0') or crc32(content,0xffffffff)!=entry.data['crc']:raise FactoryError('工厂NVS字符串CRC不符')
            value=content[:-1].decode('utf-8')
        else:raise FactoryError('工厂NVS字段类型错误')
        if entry.key in values:raise FactoryError('工厂NVS有重复有效字段，须人工修复')
        values[entry.key]=value
    expected={'schema':1,'sn':unit['sn'],'s3':unit['macs']['s3'],'grbl':unit['macs']['grbl'],'ap':unit['macs']['ap']}
    if values!=expected:raise FactoryError('工厂NVS身份不匹配')


class Grbl:
    def __init__(self,port):
        import serial
        self.link=serial.Serial(port=None,baudrate=115200,timeout=.1)
        self.link.port=port;self.link.dtr=False;self.link.rts=False
    def __enter__(self):self.link.open();return self
    def __exit__(self,*_):self.link.close()
    def query(self,command,timeout=5):
        self.link.write(command.encode()+ (b'' if command=='?' else b'\n'))
        end=time.monotonic()+timeout;lines=[]
        while time.monotonic()<end:
            line=self.link.readline().decode('utf-8','replace').strip()
            if not line:continue
            lines.append(line)
            if line.startswith(('error:','ALARM:')):raise FactoryError('写字机拒绝操作')
            if line=='ok' or (command=='?' and line.startswith('<')):return '\n'.join(lines)
        raise FactoryError('写字机应答超时，先核对原状态，不自动重发')
    def snapshot(self,expected):
        identity=self.query('$I')
        lines=identity.splitlines()
        # SSID可包含MAC=文字；限定唯一完整STA报告的末尾字段，不能借用AP或截断回包。
        reports=[line for line in lines if line.startswith('[MSG:Mode=STA:SSID=')]
        found=(re.fullmatch(r'\[MSG:Mode=STA:SSID=.*:Status=(?:Connected|Not connected):'
                            r'IP=[0-9]+(?:\.[0-9]+){3}:MAC=([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})\]',reports[0])
               if len(reports)==1 else None)
        if not found or mac(found[1].replace('-',':'))!=expected:raise FactoryError('Grbl身份不符')
        if not any(re.fullmatch(r'\[VER:1\.3a\.20260910:[^\]]*\]',line) for line in lines):
            raise FactoryError('不是量产nopaper固件')
        state=self.query('?')
        if '<Idle|MPos:0.000,0.000,0.000|' not in state:raise FactoryError('必须先人工确认Idle与XYZ原点')
        values={k:float(v) for k,v in re.findall(r'^\$(\d+)=(-?[0-9.]+)',self.query('$$'),re.M)}
        if len(values)<20:raise FactoryError('参数表不完整')
        return values


def apply_parameters(store,sn,recipe,port,operator,directory,execute=False):
    with store.operation(sn,port,operator):
        return _apply_parameters(store,sn,recipe,port,operator,directory,execute)


def _apply_parameters(store,sn,recipe,port,operator,directory,execute=False):
    unit=store.unit(sn)
    if digest_json(recipe)!=unit['recipe_sha']:raise FactoryError('配方不是该台冻结版本')
    if not recipe.get('parameters_approved_by') or not recipe.get('parameters_approved_at'):
        raise FactoryError('量产参数未经批准')
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(sn+f'-g{unit["generation"]}-parameters.json')
    if execute and path.exists():raise FactoryError('该代次已有参数操作记录；核对状态后走返修新代次')
    report={'sn':sn,'generation':unit['generation'],'planned':recipe['settings'],'executed':False}
    with Grbl(port) as link:
        before=link.snapshot(unit['macs']['grbl']);report['before']=before
        if any(before.get(k)!=v for k,v in recipe['readonly'].items()):raise FactoryError('只读安全指纹不符，隔离返修')
        if not execute:return report
        with path.open('x',encoding='utf-8') as out:json.dump(report,out)
        store.event(sn,'parameters','pending',operator,path,{'intent':True})
        try:
            for key,value in recipe['settings'].items():
                if before[key]!=value:link.query('$'+key+'='+str(value))
            after=link.snapshot(unit['macs']['grbl'])
            if compare_settings(after,recipe):raise FactoryError('参数读回不符')
            if {k:v for k,v in after.items() if k not in recipe['settings']}!={k:v for k,v in before.items() if k not in recipe['settings']}:
                raise FactoryError('非配方参数发生变化')
            report.update(after=after,executed=True,passed=True)
        except Exception as exc:
            report.update(passed=False,error=type(exc).__name__)
            path.write_text(json.dumps(report,indent=2),encoding='utf-8')
            store.event(sn,'parameters','fail',operator,path,report)
            raise
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    store.event(sn,'parameters','pass',operator,path,report)
    return report


def nvs_image(unit,output:Path,generator:Path):
    # 仅factory namespace，绝不复制用户WiFi、P-256私钥、小智token或Grbl授权。
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise FactoryError('独立NVS目标已存在')
    source=output.with_suffix('.csv')
    with source.open('x',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['key','type','encoding','value'])
        writer.writerow(['hutuji_factory','namespace','',''])
        for key,value in [('schema',1),('sn',unit['sn']),('s3',unit['macs']['s3']),('grbl',unit['macs']['grbl']),('ap',unit['macs']['ap'])]:
            writer.writerow([key,'data','u8' if key=='schema' else 'string',value])
    python=os.environ.get('HUTUJI_FACTORY_IDF_PYTHON',sys.executable)
    result=subprocess.run([python,str(generator),'generate',str(source),str(output),'0x4000'],capture_output=True,timeout=30)
    if result.returncode:raise FactoryError('NVS生成失败；请设置HUTUJI_FACTORY_IDF_PYTHON为装有IDF依赖的Python')
    if output.stat().st_size!=0x4000:raise FactoryError('NVS生成大小错误')
    verify_factory_nvs(output.read_bytes(),unit,generator)


def esptool(args):
    python=os.environ.get('HUTUJI_FACTORY_IDF_PYTHON',sys.executable)
    result=subprocess.run([python,'-m','esptool',*args],capture_output=True,timeout=180)
    if result.returncode:raise FactoryError('esptool失败，禁止继续；先检查原设备状态')
    return (result.stdout+result.stderr).decode('utf-8','replace')


def flash(store,sn,recipe,manifest_path,kind,port,operator,directory,generator,execute=False,mode='first'):
    with store.operation(sn,port,operator):
        return _flash(store,sn,recipe,manifest_path,kind,port,operator,directory,generator,execute,mode)


def _flash(store,sn,recipe,manifest_path,kind,port,operator,directory,generator,execute=False,mode='first'):
    if kind not in ('s3','grbl') or mode not in ('first','repair'):raise FactoryError('无效烧录机型/模式')
    if execute and (manifest_path.parent/'NOT_FOR_RELEASE').exists():raise FactoryError('候选演练包不能写设备')
    unit=store.unit(sn);manifest=validate_manifest(manifest_path,recipe)
    if digest_json(manifest)!=unit['manifest_sha'] or digest_json(recipe)!=unit['recipe_sha']:
        raise FactoryError('设备冻结清单不一致')
    chip='esp32s3' if kind=='s3' else 'esp32'
    segments=manifest[kind]['segments']
    if mode=='repair':segments=[s for s in segments if s['offset'] in ((0xd000,0x20000) if kind=='s3' else (0xe000,0x10000))]
    common=['--chip',chip,'--port',port,'--before','no-reset','--after','no-reset']
    plan={'sn':sn,'kind':kind,'mode':mode,'port':port,'expected_mac':unit['macs'][kind],'segments':segments,'executed':False}
    if not execute:return plan
    checks=store.status(sn)['checks']
    if any(checks[name]['result']!='pass' for name in ('flash_s3','flash_grbl') if name in checks):
        raise FactoryError('本代次有失败或待确认烧录，核对后先登记返修，禁止继续写另一板')
    directory.mkdir(parents=True,exist_ok=True)
    intent=directory/(sn+f'-g{unit["generation"]}-{kind}-flash.json')
    if intent.exists():raise FactoryError('已有本代次烧录意图，不能重复盲刷；先核对或新建返修代次')
    backup=directory/(sn+f'-g{unit["generation"]}-{kind}-backup.bin')
    if backup.exists():raise FactoryError('已有备份，先检查原操作')
    plan.update(backup_complete=False,write_attempted=False)
    attachments={}
    with intent.open('x',encoding='utf-8') as stream:json.dump(plan,stream,indent=2)
    store.event(sn,'flash_'+kind,'pending',operator,intent,{'intent':True})

    def verify_mac():
        text=esptool([*common,'read-mac'])
        found=re.findall(r'(?:MAC|Base MAC):\s*([0-9a-fA-F:]{17})',text)
        if not found or mac(found[-1])!=unit['macs'][kind]:raise FactoryError('ROM读回MAC不符')

    def record(result):
        intent.write_text(json.dumps(plan,indent=2),encoding='utf-8')
        store.event(sn,'flash_'+kind,result,operator,intent,plan,attachments=attachments,
                    expected_generation=unit['generation'])

    try:
        verify_mac()
        # 在任何写入前把整片副本及摘要登记为附件；失败台也能从档案找回备份。
        esptool([*common,'read-flash','0','0x1000000' if kind=='s3' else '0x400000',str(backup)])
        if backup.stat().st_size!=(0x1000000 if kind=='s3' else 0x400000):raise FactoryError('整片备份不完整')
        plan.update(backup=str(backup),backup_complete=True,backup_sha256=digest(backup),backup_bytes=backup.stat().st_size)
        attachments={'flash_backup':backup}
        record('pending')
        saved=store.status(sn)['checks']['flash_'+kind]['attachments']
        attachments={'flash_backup':Path(next(item['path'] for item in saved if item['name']=='flash_backup'))}
        if mode=='repair':
            with backup.open('rb') as stream:
                for segment in manifest[kind]['segments']:
                    if segment in segments:continue
                    image=(manifest_path.parent/segment['path']).read_bytes()
                    stream.seek(segment['offset'])
                    if stream.read(len(image))!=image:
                        raise FactoryError('返修包的启动/分区/资源与现机不一致，须专门迁移流程')
        with backup.open('rb') as stream:
            stream.seek(0x9000);nvs=stream.read(0x4000 if kind=='s3' else 0x5000)
        if mode=='first' and any(b!=255 for b in nvs):raise FactoryError('首次烧录仅允许空白NVS；已配置设备须返修模式')
        parts=[(s['offset'],(manifest_path.parent/s['path']).resolve()) for s in segments]
        if kind=='s3' and mode=='repair':
            # 返修保持原NVS，但不接受别台克隆的factory身份；用户身份不可被本工具重建。
            verify_factory_nvs(nvs,unit,generator)
        if kind=='s3' and mode=='first':
            own_nvs=directory/(sn+f'-g{unit["generation"]}-factory-nvs.bin');nvs_image(unit,own_nvs,generator);parts.append((0x9000,own_nvs))
        argv=[str(value) for offset,file in parts for value in (hex(offset),file)]
        # 写入前重新核验发布包，防计划与执行之间文件被替换。
        if digest_json(validate_manifest(manifest_path,recipe))!=unit['manifest_sha']:
            raise FactoryError('写入前发布清单发生变化')
        verify_mac()
        plan['write_attempted']=True
        record('pending')
        esptool([*common,'write-flash','--flash-mode','dio','--flash-size','16MB' if kind=='s3' else '4MB',*argv])
        esptool([*common,'verify-flash',*argv])
        if kind=='s3':
            esptool([*common[:-1],'hard-reset','run'])
        plan.update(executed=True,passed=True)
    except Exception as exc:
        plan.update(passed=False,error=type(exc).__name__)
        if backup.is_file() and not attachments:
            attachments={'partial_backup':backup}
        record('fail')
        raise
    record('pass')
    # 不自动拉DTR复位运动机；工位按提示正常断电重启并重新完成原点与配对验收。
    return {**plan,'next':'按工位SOP正常重启两板，再验身份、参数与配对'}
