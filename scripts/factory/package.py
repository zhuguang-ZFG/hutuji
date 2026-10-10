"""从明确已构建输入生成可搬到其他工位的自包含发布目录。"""
import argparse
import json
from pathlib import Path
import shutil

from .core import BOARD, FactoryError, read_json,validate_recipe,validate_manifest,digest,digest_json


def build(recipe_path:Path,inputs_path:Path,output:Path):
    recipe=read_json(recipe_path);validate_recipe(recipe);inputs=read_json(inputs_path)
    if output.exists():raise FactoryError('发布目录必须全新，禁止覆盖已冻结包')
    output.mkdir(parents=True)
    manifest={'schema':1,'board':BOARD,'sku':'nopaper','recipe_id':recipe['id'],'recipe_sha256':digest_json(recipe)}
    for kind in ('s3','grbl'):
        item=inputs[kind];segments=[]
        directory=output/kind;directory.mkdir()
        for index,segment in enumerate(item['segments']):
            source=Path(segment['source']).resolve()
            destination=directory/f'{index:02d}.bin'
            shutil.copyfile(source,destination)
            segments.append({'offset':segment['offset'],'path':destination.relative_to(output).as_posix(),
                             'sha256':digest(destination),'size':destination.stat().st_size})
        manifest[kind]={'commit':item['commit'],'version':item.get('version',''),'segments':segments}
    shutil.copyfile(recipe_path,output/'recipe.json')
    path=output/'manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    try:validate_manifest(path,recipe)
    except Exception:
        (output/'INVALID').write_text('校验失败，不得用于烧录',encoding='utf-8');raise
    return {'manifest':str(path),'manifest_sha256':digest_json(manifest)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='固件发布包构建：输入已编译的明确镜像及提交')
    parser.add_argument('--recipe',type=Path,required=True);parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(build(args.recipe,args.inputs,args.output),ensure_ascii=False,indent=2))
