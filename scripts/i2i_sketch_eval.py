"""对本机样张跑魔搭 i2i，落盘供「艺术家素描」目视。不入库、不写凭据。

用法（PowerShell）:
  $env:HUTUJI_MODELSCOPE_TOKEN='...'   # 或 MODELSCOPE_TOKEN
  .\\mcp-server\\.venv\\Scripts\\python.exe scripts\\i2i_sketch_eval.py `
      --input-dir results/local-only/i2i-samples `
      --out-dir results/local-only/i2i-eval

缺 token 或目录为空时 exit 2，并打印中文原因。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

from hutuji_mcp import modelscope_client  # noqa: E402


def _token() -> str:
    return (
        os.environ.get("HUTUJI_MODELSCOPE_TOKEN", "").strip()
        or os.environ.get("MODELSCOPE_TOKEN", "").strip()
    )


async def _one(client: httpx.AsyncClient, token: str, src: Path, dest: Path) -> None:
    raw = src.read_bytes()
    out = await modelscope_client.generate_i2i_png(client, token, raw)
    dest.write_bytes(out)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()
    token = _token()
    if not token:
        print("缺少 HUTUJI_MODELSCOPE_TOKEN / MODELSCOPE_TOKEN", file=sys.stderr)
        return 2
    files = sorted(
        [
            *args.input_dir.glob("*.png"),
            *args.input_dir.glob("*.jpg"),
            *args.input_dir.glob("*.jpeg"),
            *args.input_dir.glob("*.webp"),
        ]
    )
    if not files:
        print(f"样张目录为空: {args.input_dir}", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = args.out_dir / stamp
    out_root.mkdir(parents=True, exist_ok=True)

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120.0) as client:
            for src in files:
                sub = out_root / src.stem
                sub.mkdir(parents=True, exist_ok=True)
                (sub / f"in{src.suffix.lower()}").write_bytes(src.read_bytes())
                print(f"精修中 {src.name} …")
                await _one(client, token, src, sub / "out.png")
                print(f"  -> {sub / 'out.png'}")

    asyncio.run(run())
    print(f"完成 {len(files)} 张，目录 {out_root}")
    print("目视及格表: 像主题 / 素描排线 / 干净可画 / 愿称素描作品")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
