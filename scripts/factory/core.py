"""产线数据契约：任何缺失、错配或未验结果均不得产生放行记录。"""
from __future__ import annotations

import hashlib
from functools import reduce
import json
import math
from operator import xor
from pathlib import Path
import re
import struct
from typing import Any

BOARD = "freenove-esp32s3-display-2.8-lcd"
CHECKS = ("flash_s3", "flash_grbl", "parameters", "pairing", "authorization", "voice",
          "touch", "paper", "aging", "appearance", "label")


class FactoryError(ValueError):
    pass


def mac(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
        raise FactoryError("MAC必须为六组十六进制字节")
    value = value.lower()
    if int(value[:2], 16) & 1 or value in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        raise FactoryError("无效设备MAC")
    return value


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", value):
        raise FactoryError("编号仅允许1至48位字母、数字、下划线或短横线")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FactoryError("配置必须为JSON对象")
    return value


def validate_recipe(recipe: dict) -> None:
    if recipe.get("schema") != 1 or recipe.get("sku") != "nopaper" or recipe.get("board") != BOARD:
        raise FactoryError("只支持已定版量产nopaper/Freenove配方")
    identifier(recipe.get("id"))
    settings = recipe.get("settings")
    # 只允许调整已确认的速度/加速度；其余机设作为只读指纹，不写授权或行程。
    bounds = {"110": (3000,16000), "111": (3000,16000), "112": (100,3000),
              "120": (1,12000), "121": (1,12000), "122": (1,3000)}
    if not isinstance(settings, dict) or set(settings) != set(bounds):
        raise FactoryError("配方必须完整列出三轴速度和加速度")
    for key, (low, high) in bounds.items():
        value = settings[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not low <= value <= high:
            raise FactoryError("配方参数越界")
    if recipe.get("readonly") != {"1": 255, "20": 0, "21": 0, "22": 0}:
        raise FactoryError("配方必须保留四项安全只读指纹")
    acceptance = recipe.get("acceptance", {})
    for key in ("aging_hours", "aging_jobs", "max_error_mm"):
        value = acceptance.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
            raise FactoryError("验收阈值必须为正有限数")
    if type(acceptance['aging_jobs']) is not int:
        raise FactoryError('老化次数必须为整数')


def approved(recipe: dict) -> None:
    validate_recipe(recipe)
    if not recipe.get("approved_by") or not recipe.get("approved_at"):
        raise FactoryError("配方/首批试行标准尚未由负责人批准；只可预演")


def _validate_esp_image(blob: bytes, kind: str) -> None:
    # ESP-IDF/esptool的24字节头中，chip_id在12，附加SHA标志在23。
    chip_id=9 if kind=='s3' else 0
    flash_size=4 if kind=='s3' else 2
    if (len(blob)<32 or blob[0]!=0xe9 or not 1<=blob[1]<=16 or blob[2]!=2
            or blob[3]>>4!=flash_size or int.from_bytes(blob[12:14],'little')!=chip_id
            or blob[23] not in (0,1)):
        raise FactoryError('启动/应用镜像的芯片、DIO或Flash容量不符合目标板')
    position=24;checksum=0xef
    for _ in range(blob[1]):
        if position+8>len(blob):raise FactoryError('固件段头截断')
        _,length=struct.unpack_from('<II',blob,position)
        position+=8
        if position+length>len(blob):raise FactoryError('固件段数据截断')
        checksum=reduce(xor,blob[position:position+length],checksum)
        position+=length
    checksum_at=(position//16+1)*16-1
    if checksum_at>=len(blob) or blob[checksum_at]!=checksum:
        raise FactoryError('固件内部校验和不符')
    end=checksum_at+1
    if blob[23]:
        if blob[end:end+32]!=hashlib.sha256(blob[:end]).digest():
            raise FactoryError('固件内部SHA不符')
        end+=32
    if any(value not in (0,255) for value in blob[end:]):
        raise FactoryError('固件尾部存在不支持的数据')


def _validate_partitions(raw: bytes,kind: str,max_size: int) -> None:
    expected=(
        {'nvs':(1,2,0x9000,0x4000),'otadata':(1,0,0xd000,0x2000),'phy_init':(1,1,0xf000,0x1000),
         'ota_0':(0,0x10,0x20000,0x3f0000),'ota_1':(0,0x11,0x410000,0x3f0000),'assets':(1,0x82,0x800000,0x800000)}
        if kind=='s3' else
        {'nvs':(1,2,0x9000,0x5000),'otadata':(1,0,0xe000,0x2000),
         'app0':(0,0x10,0x10000,0x1e0000),'app1':(0,0x11,0x1f0000,0x1e0000),'spiffs':(1,0x82,0x3d0000,0x30000)})
    partitions={};ranges=[]
    for pos in range(0,len(raw)-31,32):
        magic,ptype,subtype,offset,length,label,flags=struct.unpack_from('<HBBII16sI',raw,pos)
        if magic==0xffff:break
        if magic==0xebeb:
            if raw[pos+16:pos+32]!=hashlib.md5(raw[:pos]).digest():
                raise FactoryError('分区表内部MD5不符')
            break
        if magic!=0x50aa:raise FactoryError('分区表格式错误')
        try:name=label.split(b'\0',1)[0].decode('ascii')
        except UnicodeDecodeError as exc:raise FactoryError('分区名称错误') from exc
        if (not name or name in partitions or flags!=0 or length<=0 or offset<0x9000
                or offset+length>max_size or any(offset<b and offset+length>a for a,b in ranges)):
            raise FactoryError('分区重复、重叠、越界或标志不支持')
        partitions[name]=(ptype,subtype,offset,length);ranges.append((offset,offset+length))
    if partitions!=expected:raise FactoryError('实际双槽分区表不符合量产板型')


def validate_manifest(path: Path, recipe: dict) -> dict:
    validate_recipe(recipe)
    if (path.parent/'INVALID').exists():
        raise FactoryError('发布目录标记无效，必须重新出包')
    data = read_json(path)
    if data.get("schema") != 1 or data.get("recipe_id") != recipe["id"]:
        raise FactoryError("发布清单与配方不匹配")
    if data.get("board") != BOARD or data.get("sku") != "nopaper":
        raise FactoryError("发布板型错误")
    if data.get("recipe_sha256") != digest_json(recipe):
        raise FactoryError("配方内容与冻结摘要不一致")
    for kind, required, max_size in (("s3", {0,0x8000,0xd000,0x20000,0x800000},0x1000000),
                                     ("grbl", {0x1000,0x8000,0xe000,0x10000},0x400000)):
        item=data.get(kind, {})
        if not isinstance(item,dict):raise FactoryError('固件条目必须为对象')
        if not re.fullmatch(r"[0-9a-f]{40}",str(item.get("commit",""))):
            raise FactoryError("固件必须冻结完整提交号")
        if not isinstance(item.get("segments"), list):
            raise FactoryError("缺少完整首烧分区清单")
        segments=item["segments"]
        if not all(isinstance(seg,dict) and type(seg.get('offset')) is int for seg in segments):
            raise FactoryError('镜像条目或地址无效')
        if {seg.get("offset") for seg in segments} != required or len(segments)!=len(required):
            raise FactoryError("首烧分区地址不符合已验证布局")
        ranges=[]
        for seg in segments:
            file=(path.parent/seg["path"]).resolve()
            if not file.is_relative_to(path.parent.resolve()) or not file.is_file():
                raise FactoryError("镜像路径越界或不存在")
            if digest(file)!=seg.get("sha256") or file.stat().st_size!=seg.get("size"):
                raise FactoryError("镜像摘要或大小不符")
            if type(seg.get('size')) is not int or type(seg.get('offset')) is not int:
                raise FactoryError("镜像地址和大小必须为整数")
            start=seg["offset"];end=start+seg["size"]
            if seg["size"]<=0 or end>max_size or any(start<b and end>a for a,b in ranges):
                raise FactoryError("分区越界或重叠")
            # 两种布局NVS均在0x9000，首烧由每台独立生成；任何镜像不能覆盖它。
            nvs_end=0xd000 if kind=="s3" else 0xe000
            if start<nvs_end and end>0x9000:
                raise FactoryError("发布包不得携带整台NVS")
            ranges.append((start,end))
        # 验证实际二进制分区表；清单里的正确地址不能掩盖错误板型的分区镜像。
        table=next(seg for seg in segments if seg['offset']==0x8000)
        raw=(path.parent/table['path']).read_bytes()
        _validate_partitions(raw,kind,max_size)
        boot=next(seg for seg in segments if seg['offset']==(0 if kind=='s3' else 0x1000))
        _validate_esp_image((path.parent/boot['path']).read_bytes(),kind)
        app_offset=0x20000 if kind=='s3' else 0x10000
        app=next(s for s in segments if s['offset']==app_offset)
        blob=(path.parent/app['path']).read_bytes()
        _validate_esp_image(blob,kind)
        if len(blob)>(0x3f0000 if kind=='s3' else 0x1e0000):
            raise FactoryError('应用超出OTA分区')
        if kind=='s3':
            version=str(item.get('version',''))
            # IDF应用描述位于首个段开头，不能以正文里碰巧出现的版本文字作证。
            if (len(blob)<112 or struct.unpack_from('<I',blob,32)[0]!=0xabcd5432
                    or struct.unpack_from('<I',blob,28)[0]<256
                    or not re.fullmatch(r'hutuji\.\d+\.\d+\.\d+',version)
                    or blob[48:80].split(b'\0',1)[0]!=version.encode()
                    or blob[80:112].split(b'\0',1)[0]!=b'xiaozhi'):
                raise FactoryError('应用版本与发布清单不一致')
            if BOARD.encode()+b'\0' not in blob:
                raise FactoryError('应用未包含量产Freenove板型标识')
    return data


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def compare_settings(actual: dict[str,float], recipe: dict) -> list[str]:
    return [key for key,value in {**recipe["readonly"],**recipe["settings"]}.items()
            if key not in actual or not math.isfinite(actual[key]) or abs(actual[key]-value)>1e-5]


def validate_measurements(checks: dict, recipe: dict) -> None:
    """数值缺失、NaN、布尔和负数不能通过质量门。"""
    aging=json.loads(checks['aging']['detail'])
    paper=json.loads(checks['paper']['detail'])
    limits=recipe['acceptance']
    for values,key,integer in ((aging,'hours',False),(aging,'jobs',True),
                               (aging,'errors',True),(paper,'max_error_mm',False),
                               (paper,'missed_strokes',True),(paper,'lost_steps',True)):
        value=values.get(key)
        if type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or value<0:
            raise FactoryError('测量缺失或无效: '+key)
    if (aging['hours']<limits['aging_hours'] or aging['jobs']<limits['aging_jobs'] or
        aging['errors']!=0 or paper['max_error_mm']>limits['max_error_mm'] or
        paper['missed_strokes']!=0 or paper['lost_steps']!=0):
        raise FactoryError('测量未达到配方验收阈值')
