"""运行 python -m scripts.factory --help 获取产线命令；默认只预演硬件动作。"""
import argparse
import csv
import json
from pathlib import Path
import sys

from .core import BOARD, CHECKS, FactoryError, approved, digest_json, read_json, validate_manifest, validate_recipe
from .store import Store


def main():
    parser=argparse.ArgumentParser(description='量产nopaper工位：登记→预演/烧录→参数→验收→放行')
    parser.add_argument('--db',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('register');p.add_argument('--sn',required=True);p.add_argument('--batch',required=True)
    for name in ('s3','grbl','ap'):p.add_argument('--'+name+'-mac',required=True)
    p.add_argument('--recipe',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
    for name in ('status','export'):
        p=sub.add_parser(name);p.add_argument('--sn',required=True)
        if name=='export':p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('batch');p.add_argument('--batch',required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('backup');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('archive');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('record');p.add_argument('--sn',required=True);p.add_argument('--check',choices=CHECKS,required=True)
    p.add_argument('--result',choices=('pass','fail','pending'),required=True);p.add_argument('--operator',required=True)
    p.add_argument('--evidence',type=Path,required=True);p.add_argument('--detail',type=Path,required=True)
    p=sub.add_parser('release');p.add_argument('--sn',required=True);p.add_argument('--operator',required=True)
    p.add_argument('--recipe',type=Path,required=True);p.add_argument('--delivery',type=Path,required=True)
    p=sub.add_parser('rework');p.add_argument('--sn',required=True);p.add_argument('--operator',required=True);p.add_argument('--reason',required=True)
    p=sub.add_parser('resolve');p.add_argument('--sn',required=True);p.add_argument('--operator',required=True);p.add_argument('--evidence',type=Path,required=True)
    p=sub.add_parser('inspect');p.add_argument('--sn',required=True);p.add_argument('--operator',required=True)
    p.add_argument('--recipe',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--evidence-dir',type=Path,required=True)
    for name in ('parameters','flash'):
        p=sub.add_parser(name);p.add_argument('--sn',required=True);p.add_argument('--port',required=True)
        p.add_argument('--operator',required=True);p.add_argument('--recipe',type=Path,required=True)
        p.add_argument('--evidence-dir',type=Path,required=True);p.add_argument('--execute',action='store_true')
        if name=='flash':
            p.add_argument('--manifest',type=Path,required=True);p.add_argument('--kind',choices=('s3','grbl'),required=True)
            p.add_argument('--mode',choices=('first','repair'),default='first');p.add_argument('--nvs-generator',type=Path,required=True)
    p=sub.add_parser('test-artifacts');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('delivery');p.add_argument('--details',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();store=Store(args.db);cmd=args.command
    if cmd=='register':
        recipe=read_json(args.recipe);validate_recipe(recipe);manifest=validate_manifest(args.manifest,recipe)
        store.register(args.sn,args.batch,BOARD,digest_json(recipe),digest_json(manifest),args.s3_mac,args.grbl_mac,args.ap_mac)
        result=store.status(args.sn)
    elif cmd in ('status','export'):
        result=store.history(args.sn) if cmd=='export' else store.status(args.sn)
        if cmd=='export':
            if args.output.exists():raise FactoryError('导出目标已存在')
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    elif cmd=='batch':
        with store.connect() as db:units=db.execute('SELECT sn FROM units WHERE batch=? ORDER BY sn',(args.batch,)).fetchall()
        with args.output.open('x',newline='',encoding='utf-8-sig') as stream:
            writer=csv.writer(stream);writer.writerow(['sn','generation','released','s3_mac','grbl_mac','missing'])
            for row in units:
                item=store.status(row['sn']);writer.writerow([item['sn'],item['generation'],item['released'],item['macs']['s3'],item['macs']['grbl'],';'.join(item['missing'])])
        result={'exported':len(units)}
    elif cmd=='backup':store.backup(args.output);result={'backup':str(args.output)}
    elif cmd=='archive':store.archive(args.output);result={'archive':str(args.output)}
    elif cmd=='record':
        # 烧录/参数只能由真实执行器记pass，禁止人工手写冒充自动检验。
        if args.check in ('flash_s3','flash_grbl','parameters','pairing','authorization'):raise FactoryError('该项由执行器自动记账')
        detail=read_json(args.detail)
        if detail.get('sn')!=args.sn:raise FactoryError('测量证据SN不符')
        if type(detail.get('generation')) is not int or detail['generation']<1:
            raise FactoryError('测量证据必须填写status显示的验收代次generation')
        if args.check=='aging' and args.result=='pass':
            if detail.get('errors')!=0 or not detail.get('hours') or not detail.get('jobs'):raise FactoryError('老化记录必须包含时长、次数与零失败')
        store.event(args.sn,args.check,args.result,args.operator,args.evidence,detail,
                    expected_generation=detail['generation']);result=store.status(args.sn)
    elif cmd=='release':
        store.release(args.sn,args.operator,read_json(args.recipe),read_json(args.delivery));result=store.status(args.sn)
    elif cmd=='rework':store.rework(args.sn,args.operator,args.reason);result=store.status(args.sn)
    elif cmd=='resolve':store.resolve_operation(args.sn,args.operator,args.evidence);result=store.status(args.sn)
    elif cmd=='inspect':
        from .inspect import inspect_device
        with store.operation(args.sn,'inspect:'+args.sn,args.operator):
            result=inspect_device(store,args.sn,read_json(args.recipe),args.manifest,args.operator,args.evidence_dir)
    elif cmd in ('parameters','flash'):
        from .hardware import apply_parameters,flash
        recipe=read_json(args.recipe);validate_recipe(recipe)
        if cmd=='parameters':result=apply_parameters(store,args.sn,recipe,args.port,args.operator,args.evidence_dir,args.execute)
        else:result=flash(store,args.sn,recipe,args.manifest,args.kind,args.port,args.operator,args.evidence_dir,args.nvs_generator,args.execute,args.mode)
    elif cmd=='test-artifacts':
        from .documents import test_artifacts
        result=test_artifacts(args.output)
    else:
        from .documents import delivery
        result=delivery(read_json(args.details),args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    try:main()
    except (FactoryError,OSError,ValueError) as exc:
        print('失败：'+str(exc),file=sys.stderr);raise SystemExit(1)
