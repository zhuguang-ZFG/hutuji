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
    assert prep.GATE_VERSION == "4"
    assert manifest["categories"]["cat"]["count"] >= 3
    assert manifest["ready"] is False
    assert (audit_dir / "cat.txt").read_text(encoding="utf-8").count("\n") == 3


def test_prep_stops_whole_run_when_image_backends_fail(tmp_path: Path, monkeypatch):
    prep = _load_prep()
    calls: list[str] = []

    def failing_candidate(category: str, fixture, *, attempt: int, slot: int) -> str:
        calls.append(category)
        raise RuntimeError("商汤生图 API 返回 404")

    monkeypatch.setattr(prep, "_candidate_svg", failing_candidate)
    monkeypatch.setattr(prep, "unique_categories", lambda: ["cat", "dog", "crab"])

    code = prep.main([
        "--out", str(tmp_path / "out"),
        "--audit-dir", str(tmp_path / "audit"),
        "--per-category", "1",
        "--max-attempts-per-slot", "5",
    ])

    # 10-31 生产循环：每个未满类目都把四个后端打一遍，每 90s 一轮。
    assert code == 1
    assert calls == ["cat"]
    assert (tmp_path / "out" / "manifest.json").exists()


def test_prep_audit_write_failure_does_not_stop_other_categories(tmp_path: Path, monkeypatch):
    prep = _load_prep()
    seen: list[str] = []

    def fill(category: str, **_kwargs) -> None:
        seen.append(category)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(prep, "fill_category", fill)
    monkeypatch.setattr(prep, "unique_categories", lambda: ["cat", "dog"])

    assert prep.main(["--out", str(tmp_path / "out"), "--audit-dir", str(tmp_path / "audit")]) == 1
    assert seen == ["cat", "dog"]
