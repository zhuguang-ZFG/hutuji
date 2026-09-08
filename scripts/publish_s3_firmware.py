"""发布小派 S3 固件到静态仓：manifest.json + catalog 更新 + latest.json。

用法（hub 根，PYTHONPATH 含 draw-portal）::

  python scripts/publish_s3_firmware.py \\
    --board freenove-esp32s3-display-2.8-lcd \\
    --version hutuji.1.0.1 \\
    --bin path/to/xiaozhi.bin \\
    --catalog path/to/catalog.json \\
    --outdir /var/hutuji/firmware \\
    [--public-base https://hutuji.donglicao.com] \\
    [--notes "修复说明"]

产物布局::

  {outdir}/s3/{board}/{version}/xiaozhi.bin
  {outdir}/s3/{board}/{version}/manifest.json
  {outdir}/s3/{board}/latest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from draw_portal.firmware_version import parse_hutuji_version

_DEVICE = "s3"
_BIN_NAME = "xiaozhi.bin"
_LATEST_FIELDS = ("device", "board", "version", "url", "sha256", "size", "notes")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_url(public_base: str, relpath: str) -> str:
    base = public_base.rstrip("/")
    rel = relpath.lstrip("/")
    return f"{base}/firmware/{rel}"


def _relpath(board: str, version: str) -> str:
    return f"s3/{board}/{version}/{_BIN_NAME}"


def _load_catalog(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("catalog root must be object")
    boards = data.setdefault("boards", {})
    if not isinstance(boards, dict):
        raise ValueError("catalog.boards must be object")
    data.setdefault("device", _DEVICE)
    return data


def publish_firmware(
    *,
    board: str,
    version: str,
    bin_path: Path,
    catalog_path: Path,
    outdir: Path,
    public_base: str,
    notes: str = "",
) -> dict[str, Any]:
    board = board.strip()
    version = version.strip()
    if not board:
        raise ValueError("board 不能为空")
    if parse_hutuji_version(version) is None:
        raise ValueError(f"version 须匹配 hutuji.x.y.z：{version!r}")

    if not bin_path.is_file():
        raise FileNotFoundError(f"bin 不存在：{bin_path}")

    digest = _sha256_file(bin_path)
    size = bin_path.stat().st_size
    rel = _relpath(board, version)
    url = _build_url(public_base, rel)

    version_dir = outdir / "s3" / board / version
    version_dir.mkdir(parents=True, exist_ok=True)
    dest_bin = version_dir / _BIN_NAME
    shutil.copy2(bin_path, dest_bin)

    manifest: dict[str, Any] = {
        "device": _DEVICE,
        "board": board,
        "version": version,
        "url": url,
        "sha256": digest,
        "size": size,
        "min_compat": version,
        "notes": notes,
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    catalog = _load_catalog(catalog_path)
    catalog["boards"][board] = {
        "version": version,
        "notes": notes,
        "sha256": digest,
        "size": size,
        "relpath": rel,
    }
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    latest = {k: manifest[k] for k in _LATEST_FIELDS}
    latest_dir = outdir / "s3" / board
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest_path = latest_dir / "latest.json"
    latest_path.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "manifest": manifest,
        "latest_path": latest_path,
        "dest_bin": dest_bin,
        "digest": digest,
    }


def _run_self_test() -> None:
    payload = b"hutuji-firmware-self-test-bin"
    expected_sha = hashlib.sha256(payload).hexdigest()
    board = "test-board-self"
    version = "hutuji.9.8.7"
    public_base = "https://example.test"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bin_path = root / "fake.bin"
        bin_path.write_bytes(payload)
        catalog_path = root / "catalog.json"
        catalog_path.write_text(
            json.dumps({"device": _DEVICE, "boards": {}}),
            encoding="utf-8",
        )
        outdir = root / "firmware-out"

        result = publish_firmware(
            board=board,
            version=version,
            bin_path=bin_path,
            catalog_path=catalog_path,
            outdir=outdir,
            public_base=public_base,
            notes="self-test",
        )

        assert result["digest"] == expected_sha, "sha256 与假 bin 不符"
        copied = result["dest_bin"].read_bytes()
        assert copied == payload, "复制后的 bin 字节不一致"

        latest = json.loads(result["latest_path"].read_text(encoding="utf-8"))
        for field in _LATEST_FIELDS:
            assert field in latest, f"latest.json 缺字段 {field}"
        assert latest["device"] == _DEVICE
        assert latest["board"] == board
        assert latest["version"] == version
        assert latest["sha256"] == expected_sha
        assert latest["size"] == len(payload)
        assert latest["notes"] == "self-test"
        assert latest["url"] == f"{public_base}/firmware/{_relpath(board, version)}"

        manifest = json.loads((outdir / "s3" / board / version / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["sha256"] == expected_sha
        assert manifest["url"] == latest["url"]

        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        entry = catalog["boards"][board]
        assert entry["version"] == version
        assert entry["sha256"] == expected_sha
        assert entry["relpath"] == _relpath(board, version)

    print("PASS publish_s3_firmware self-test")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布小派 S3 固件到静态仓")
    parser.add_argument("--self-test", action="store_true", help="内置自测（tempfile 假 bin）")
    parser.add_argument("--board", help="板型名，如 freenove-esp32s3-display-2.8-lcd")
    parser.add_argument("--version", help="hutuji.x.y.z 版本串")
    parser.add_argument("--bin", dest="bin_path", help="xiaozhi.bin 路径")
    parser.add_argument("--catalog", dest="catalog_path", help="catalog.json 路径（就地更新）")
    parser.add_argument("--outdir", help="静态仓根目录，如 /var/hutuji/firmware")
    parser.add_argument(
        "--public-base",
        default="https://hutuji.donglicao.com",
        help="公网前缀（写入 url 字段）",
    )
    parser.add_argument("--notes", default="", help="发版说明")
    args = parser.parse_args(argv)

    if args.self_test:
        _run_self_test()
        return 0

    missing = [
        name
        for name, val in (
            ("--board", args.board),
            ("--version", args.version),
            ("--bin", args.bin_path),
            ("--catalog", args.catalog_path),
            ("--outdir", args.outdir),
        )
        if not val
    ]
    if missing:
        parser.error(f"缺少参数：{', '.join(missing)}")

    publish_firmware(
        board=args.board,
        version=args.version,
        bin_path=Path(args.bin_path),
        catalog_path=Path(args.catalog_path),
        outdir=Path(args.outdir),
        public_base=args.public_base,
        notes=args.notes,
    )
    print(f"OK board={args.board} version={args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
