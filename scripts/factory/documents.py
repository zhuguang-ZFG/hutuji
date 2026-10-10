"""生成可审查的工位测试件及客户资料；缺资料/无真实证据不得打印合格证。"""
import hashlib
import json
from pathlib import Path

from .core import FactoryError


def validate_delivery(data):
    for key in ('model','seller','support','warranty','privacy_url','source_url'):
        value=data.get(key)
        if not isinstance(value,str) or not value.strip() or any(token in value for token in ('待定','待填','【','___')):
            raise FactoryError('客户资料未完成: '+key)
    for key in ('privacy_url','source_url'):
        if not data[key].startswith('https://'):raise FactoryError('客户资料链接必须为HTTPS')
    for key in ('license_review','electrical_review','radio_review'):
        evidence=data.get(key,{})
        if not evidence.get('reviewer') or not evidence.get('date') or not Path(evidence.get('report','')).is_file():
            raise FactoryError('缺少真实签核与报告: '+key)


def delivery(data,output):
    validate_delivery(data)
    output.mkdir(parents=True,exist_ok=True)
    text=f'''# {data['model']} 使用说明

销售主体：{data['seller']}。售后：{data['support']}。

本机由小派和无自动换纸写字机组成，一次放一张纸，绘制结束需手动取换纸。
先按机身标识放置，固定纸张与笔，确认笔尖高度及原点；不要手推已使能的运动轴。
给两板供电，在小派长按进入配网，使用微信扫描屏幕二维码，登录并绑定手机号后按页面提示连接家中2.4GHz Wi-Fi。
等待小派、写字机均显示就绪再开始。若提示配对身份不符，请联系售后，不要改接邻近写字机。

在小程序选择或生成作品，核对预览与纸张后发送；也可用语音描述作品，并在预览后确认开始。
绘制中可暂停、继续或停止；停止会排空在途命令后归位，不能代替断电急停。
结束后确认笔已抬起、机器停稳，再取纸。重新放纸后重复以上步骤。

换路由器时重新扫码配网；解绑/转让从小程序设备管理完成，恢复出厂不会证明设备所有权已转让。
网络失败先检查2.4GHz无线和路由器隔离设置；反复失败保留机身序列号及错误提示联系售后。
更新按页面提示进行，升级中勿断电；版本不明时先查看状态，不重复点击升级。

保修：{data['warranty']}。
隐私说明：{data['privacy_url']}。语音和生成服务会使用云端处理。
开源许可证与对应固件源码：{data['source_url']}。
'''
    path=output/'customer-guide.md'
    with path.open('x',encoding='utf-8') as stream:stream.write(text)
    return {'guide':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def test_artifacts(output:Path):
    output.mkdir(parents=True,exist_ok=True)
    # 标尺20mm、矩形与交叉线，各边界离原点至少10mm；实际执行仍需工位确认。
    lines=['G21','G90','F24000','G1G90 Z0.0F10000','G1G90 Z0.0F10000']
    paths=[[(10,10),(30,10),(30,30),(10,30),(10,10)],[(10,20),(30,20)],[(20,10),(20,30)]]
    for points in paths:
        x,y=points[0];lines+= [f'G0 X{x:.3f} Y{y:.3f} F24000','G1G90 Z5.0F10000']
        lines += [f'G1 X{x:.3f} Y{y:.3f} F24000' for x,y in points[1:]]
        lines += ['G1G90 Z0.0F10000']
    lines += ['G1G90 Z0.0F10000']
    code='\n'.join(lines)+'\n'
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'mcp-server'))
    from hutuji_mcp.gcode_gen import validate_gcode
    validate_gcode(code,total_bytes=len(code.encode()))
    path=output/'factory-20mm.gcode'
    with path.open('x',encoding='ascii') as stream:stream.write(code)
    # SVG可从现有上传入口进入真实预览/纸张校验流程；不要求用户上传不受支持的G-code。
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" viewBox="0 0 40 40"><path d="M10 10H30V30H10Z M10 20H30 M20 10V30" fill="none" stroke="black"/></svg>'
    (output/'factory-20mm.svg').write_text(svg,encoding='utf-8')
    sample={'sn':'填写本台SN','generation':None,'hours':0,'jobs':0,'errors':0,'max_error_mm':None,'missed_strokes':None,'lost_steps':None,'operator':'','photos':[],
            'instructions':'测量20mm正方形X/Y边长、对角线与交叉中心偏差；记录漏笔、重影、失步。空值不得判pass。'}
    (output/'measurement-template.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'gcode':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'xy_max_mm':30,'execute':False}
