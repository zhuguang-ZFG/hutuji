from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_prep():
    spec = importlib.util.spec_from_file_location(
        "curated_prep",
        Path(__file__).resolve().parents[1] / "scripts" / "curated_prep.py",
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prep_fixture_fills_category_and_manifest(tmp_path: Path):
    prep = _load_prep()
    fixture = Path(__file__).parent / "fixtures" / "curated_ok.svg"
    audit_dir = tmp_path / "audit"

    prep.fill_category(
        "cat",
        per_category=3,
        out_dir=tmp_path,
        fixture=fixture,
        max_attempts=1,
        audit_dir=audit_dir,
    )
    manifest = prep.recount_and_write_manifest(tmp_path, n_target=3, n_min=3)

    assert (tmp_path / "cat" / "000.svg").exists()
    assert manifest == json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["gate_version"] == prep.GATE_VERSION
    assert prep.GATE_VERSION == "3"
    assert manifest["categories"]["cat"]["count"] >= 3
    assert manifest["ready"] is False
    assert (audit_dir / "cat.txt").read_text(encoding="utf-8").count("\n") == 3
