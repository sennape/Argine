"""R2-10 Phase 3A: schema-compatibility tests for the frozen R2-09 EVAL
workload specifications (items-schema) against the generic workload loader
and the R2-09B EVAL capture-command planner.

No model execution, no EVAL capture, no EVAL result inspection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argine.research import workloads
from argine.research2.r2_09b import eval_capture as ec

ROOT = Path(__file__).resolve().parents[1]
EVAL_MANIFEST = json.loads(
    (ROOT / "experiments/R2-09-prediction-benchmark/eval_manifest.json").read_text(encoding="utf-8")
)
SPECS = EVAL_MANIFEST["eval_workload_specs"]


def _frozen_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("spec", SPECS, ids=[s["workload_id"] for s in SPECS])
def test_frozen_file_hash_matches_manifest(spec):
    path = ROOT / spec["source_path"]
    assert _frozen_hash(path) == spec["sha256"]


@pytest.mark.parametrize("spec", SPECS, ids=[s["workload_id"] for s in SPECS])
def test_loads_as_eval_split_with_exact_semantics(spec):
    path = ROOT / spec["source_path"]
    before = path.read_bytes()
    w = workloads.load(str(path))
    after = path.read_bytes()
    assert before == after, "loading must never mutate the frozen source file"

    assert w.sha256 == spec["sha256"]
    assert set(w.prompts.keys()) == {"eval"}, "R2-09 EVAL files must expose only an 'eval' split"
    assert w.name == spec["workload_id"]

    raw = json.loads(before.decode("utf-8"))
    ev = w.split("eval")
    assert len(ev) == len(raw["items"]) == 1
    for prompt_obj, raw_item in zip(ev, raw["items"]):
        assert prompt_obj.id == raw_item["id"]
        assert prompt_obj.segment == raw_item.get("segment", raw["name"])
        assert prompt_obj.prompt == raw_item["prompt"], "prompt text must be byte-identical, unnormalized"
        assert prompt_obj.n_predict == raw.get("n_predict", 128)


def test_split_missing_raises_keyerror_not_silent_empty():
    """Before the fix this silently returned prompts={}; confirm requesting
    the 'dev' split (which these EVAL-only files never declare) still fails
    loudly rather than returning something empty/wrong."""
    spec = SPECS[0]
    w = workloads.load(str(ROOT / spec["source_path"]))
    with pytest.raises(KeyError):
        w.split("dev")


def test_generic_splits_schema_files_unaffected():
    """Every existing workload file in workloads/v1/ uses the 'splits'
    schema; the new 'items' branch must never be taken for them, so their
    behavior is byte-for-byte unchanged."""
    for name in workloads.available("v1"):
        w = workloads.load(name, "v1")
        assert set(w.prompts.keys()) <= {"dev", "eval"}
        assert len(w.prompts) >= 1


def test_items_schema_without_split_field_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "items": [{"id": "a", "prompt": "p"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        workloads.load(str(bad))


@pytest.mark.parametrize("spec", SPECS, ids=[s["workload_id"] for s in SPECS])
def test_capture_command_construction_matches_frozen_protocol(spec):
    cmd = ec.plan_capture_command(workload_id=spec["workload_id"],
                                  model_path="DUMMY_MODEL_PATH", seed=42,
                                  out_dir=Path("DUMMY_OUT"))
    assert "research" in cmd and "trace" in cmd
    assert "--expert-budget" in cmd and cmd[cmd.index("--expert-budget") + 1] == "4G"
    assert "--ctx" in cmd and cmd[cmd.index("--ctx") + 1] == "4096"
    assert "--threads" in cmd and cmd[cmd.index("--threads") + 1] == "4"
    assert "--split" in cmd and cmd[cmd.index("--split") + 1] == "eval"
    assert "--workload" in cmd and cmd[cmd.index("--workload") + 1] == spec["source_path"]
    assert "--seed" in cmd and cmd[cmd.index("--seed") + 1] == "42"
    assert "DUMMY_MODEL_PATH" in cmd


def test_seed_propagates_and_is_not_frozen_fact():
    cmd1 = ec.plan_capture_command(workload_id=SPECS[0]["workload_id"],
                                   model_path="M", seed=42, out_dir=Path("O"))
    cmd2 = ec.plan_capture_command(workload_id=SPECS[0]["workload_id"],
                                   model_path="M", seed=99, out_dir=Path("O"))
    assert cmd1[cmd1.index("--seed") + 1] == "42"
    assert cmd2[cmd2.index("--seed") + 1] == "99"


def test_capture_command_fails_closed_on_hash_drift(tmp_path, monkeypatch):
    spec = dict(SPECS[0])
    spec["sha256"] = "0" * 64  # deliberately wrong
    monkeypatch.setattr(ec, "eval_workload_specs", lambda: [spec])
    with pytest.raises(ValueError):
        ec.plan_capture_command(workload_id=spec["workload_id"], model_path="M", seed=42,
                                out_dir=Path("O"))


def test_dev_eval_separation_no_dev_split_in_r2_09_eval_files():
    for spec in SPECS:
        w = workloads.load(str(ROOT / spec["source_path"]))
        assert "dev" not in w.prompts, "R2-09 EVAL workloads must never expose a 'dev' split"


def test_eval_manifest_unchanged_on_disk():
    """This test file and everything else in this phase must never touch
    eval_manifest.json (part of r2_09b.contract.COMPONENT_FILES)."""
    from argine.research2.r2_09b import contract as c
    d = c.default_contract_dir()
    errors = c.validate_contract(d)
    assert errors == []
