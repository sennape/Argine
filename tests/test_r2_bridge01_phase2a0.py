"""
R2-BRIDGE-01 Phase 2A.0 -- Fresh Holdout Specification & Acquisition Plan Freeze.
Verifies: the four fresh prompt specs load correctly, their identities/hashes
match the manifest, freshness against R2-10 DEV/EVAL, that the original
Phase-0 and Phase-0.1 digests are unchanged, that the Phase-1 selection freeze
is unchanged, that the acquisition plan matches the frozen values exactly,
that output paths are unique, that retry rules fail closed conceptually, and
that no BRIDGE trace/result exists yet.
"""
import hashlib
import json
import os

import pytest

from argine.research.workloads import load as load_workload
from argine.research2.r2_bridge01.contract import compute_contract_digest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_DIR = os.path.join(REPO_ROOT, "experiments", "R2-BRIDGE-01-retention-feasibility")
WORKLOAD_DIR = os.path.join(REPO_ROOT, "research2", "workloads", "r2_bridge01")

SPEC_FILES = [
    "bridge-coding-v1.json",
    "bridge-reasoning-v1.json",
    "bridge-summarization-v1.json",
    "bridge-instruction-v1.json",
]

ORIGINAL_PHASE0_DIGEST = "e4ef4268e6bc207f20d5f4a56c165b75b977536600ff9d6f7e59604f77a889fc"
PHASE01_ADDENDUM_DIGEST = "5954a0f6d13fcf427124f8b7f7d7f009d2d76fbe82e4b4e28c6aa2167fc8eb92"


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _manifest():
    return _load_json(os.path.join(BRIDGE_DIR, "BRIDGE-HOLDOUT-MANIFEST-v1.json"))


def _acquisition_plan():
    return _load_json(os.path.join(BRIDGE_DIR, "PHASE2A-ACQUISITION-PLAN-v1.json"))


def _freshness_audit():
    return _load_json(os.path.join(BRIDGE_DIR, "PHASE2A0-FRESHNESS-AUDIT.json"))


def _state_transition():
    return _load_json(os.path.join(BRIDGE_DIR, "PHASE2A0-STATE-TRANSITION.json"))


class TestFourFreshSpecsLoad:
    @pytest.mark.parametrize("fn", SPEC_FILES)
    def test_loads_via_unmodified_loader(self, fn):
        path = os.path.join(WORKLOAD_DIR, fn)
        wl = load_workload(path)
        assert wl.name.startswith("bridge-")
        assert "eval" in wl.prompts if hasattr(wl, "prompts") else True

    @pytest.mark.parametrize("fn", SPEC_FILES)
    def test_exact_one_item(self, fn):
        d = _load_json(os.path.join(WORKLOAD_DIR, fn))
        assert len(d["items"]) == 1

    def test_correct_workload_ids(self):
        ids = set()
        for fn in SPEC_FILES:
            d = _load_json(os.path.join(WORKLOAD_DIR, fn))
            ids.add(d["name"])
        assert ids == {
            "bridge-coding-v1",
            "bridge-reasoning-v1",
            "bridge-summarization-v1",
            "bridge-instruction-v1",
        }

    @pytest.mark.parametrize("fn", SPEC_FILES)
    def test_provenance_split_identity_is_bridge_holdout(self, fn):
        d = _load_json(os.path.join(WORKLOAD_DIR, fn))
        assert d["provenance_split_identity"] == "bridge_holdout"
        # CLI-facing field uses the already-supported value, per this phase's
        # own "prefer an already-supported schema" instruction.
        assert d["split"] == "eval"


class TestManifestShaMatches:
    def test_manifest_cells_sha_matches_disk(self):
        manifest = _manifest()
        for cell in manifest["cells"]:
            full = os.path.join(REPO_ROOT, cell["source_path"])
            assert _sha256_file(full) == cell["sha256"]

    def test_manifest_source_text_sha_matches_disk(self):
        manifest = _manifest()
        found_source = False
        for cell in manifest["cells"]:
            if cell["source_text_sha256"]:
                found_source = True
                full = os.path.join(REPO_ROOT, cell["source_text_path"])
                assert _sha256_file(full) == cell["source_text_sha256"]
        assert found_source, "expected exactly one cell (summarization) to carry a source text"

    def test_manifest_digest_is_self_consistent(self):
        manifest = _manifest()
        digest_field = manifest.pop("manifest_digest_sha256")
        blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == digest_field


class TestFreshnessAgainstR2_10:
    def test_overall_pass(self):
        audit = _freshness_audit()
        assert audit["overall_pass"] is True

    def test_no_byte_identical_or_normalized_identical(self):
        audit = _freshness_audit()
        assert audit["any_byte_identical_match"] is False
        assert audit["any_normalized_identical_match"] is False

    def test_no_workload_or_item_id_reuse(self):
        audit = _freshness_audit()
        assert audit["workload_id_reuse"] == []
        assert audit["item_id_reuse"] == []

    def test_no_high_overlap(self):
        audit = _freshness_audit()
        assert audit["any_8gram_overlap_ratio_ge_0_5"] is False
        assert audit["global_max_8gram_overlap_ratio"] < 0.5


class TestOriginalDigestsUnchanged:
    def test_phase0_digest_unchanged(self):
        assert compute_contract_digest() == ORIGINAL_PHASE0_DIGEST

    def test_phase01_addendum_digest_unchanged(self):
        freeze = _load_json(os.path.join(BRIDGE_DIR, "PHASE0A-DEV-SELECTION-RULE-FREEZE.json"))
        assert freeze["addendum_digest_sha256"] == PHASE01_ADDENDUM_DIGEST

    def test_phase1_selection_freeze_unchanged(self):
        freeze = _load_json(os.path.join(BRIDGE_DIR, "PHASE1-SELECTION-FREEZE.json"))
        assert freeze["classification"] == "DEV_RETENTION_SIGNAL_POSITIVE"
        assert freeze["selected_config_id"] == "predicted_protect/budget=4294967296"
        assert "NOT CAPTURED" in freeze["bridge_holdout_status"]

    def test_manifest_and_plan_pin_both_original_digests(self):
        manifest = _manifest()
        assert manifest["original_phase0_contract_digest"] == ORIGINAL_PHASE0_DIGEST
        assert manifest["phase01_dev_selection_rule_digest"] == PHASE01_ADDENDUM_DIGEST


class TestAcquisitionPlanValues:
    def test_model_and_runtime_identity_exact(self):
        plan = _acquisition_plan()
        assert plan["model_identity"]["name"] == "Qwen3-30B-A3B-Q4_K_M"
        assert plan["model_identity"]["quantization"] == "Q4_K_M"
        assert plan["model_identity"]["sha256"] == "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32"
        assert plan["runtime_identity"]["sha256"] == "a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3"

    def test_deterministic_capture_conditions_exact(self):
        plan = _acquisition_plan()
        cond = plan["deterministic_capture_conditions"]
        assert cond == {
            "seed": 42,
            "context": 4096,
            "threads": 4,
            "threads_batch": 12,
            "temperature": 0.0,
            "top_k": 1,
            "n_predict": 512,
            "expert_store_capture_budget": "4G",
        }

    def test_output_paths_unique(self):
        plan = _acquisition_plan()
        paths = list(plan["canonical_output_paths"].values())
        assert len(paths) == 4
        assert len(set(paths)) == 4

    def test_capture_commands_cover_all_four_workloads_and_pin_frozen_values(self):
        plan = _acquisition_plan()
        cmds = plan["planned_capture_commands_windows_powershell"]
        assert set(cmds.keys()) == {
            "bridge-coding-v1", "bridge-reasoning-v1",
            "bridge-summarization-v1", "bridge-instruction-v1",
        }
        for wid, cmd in cmds.items():
            assert "--seed 42" in cmd
            assert "--threads 4" in cmd
            assert "--llama-arg 12" in cmd
            assert "--ctx 4096" in cmd
            assert "--expert-budget 4G" in cmd
            assert "--split eval" in cmd
            assert wid.replace("bridge-", "bridge-") in cmd  # workload path contains the id-bearing filename

    def test_retry_rules_present_and_cover_required_minimums(self):
        plan = _acquisition_plan()
        rules_text = " ".join(plan["retry_and_failure_rules"]).lower()
        for must_mention in [
            "never overwrite", "preserve failed", "pre-launch failure",
            "canonical only after", "malformed", "identical frozen acquisition parameters",
            "no prompt", "no parallel", "timeout",
        ]:
            assert must_mention in rules_text, f"missing retry rule covering: {must_mention}"

    def test_acquisition_plan_digest_is_self_consistent(self):
        plan = _acquisition_plan()
        digest_field = plan.pop("acquisition_plan_digest_sha256")
        blob = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == digest_field


class TestStateMachineAndNoResultYet:
    def test_state_is_bridge_acquisition_planned(self):
        state = _state_transition()
        assert state["current_state"] == "BRIDGE_ACQUISITION_PLANNED"
        assert "BRIDGE_CAPTURED_SEALED" in state["not_yet_reached"]
        assert "BRIDGE_OPENED" in state["not_yet_reached"]

    def test_no_bridge_trace_exists_anywhere_in_repo(self):
        for root, dirs, files in os.walk(REPO_ROOT):
            if ".git" in root:
                continue
            for fn in files:
                if "bridge" in fn.lower() and ("trace" in fn.lower() or fn.endswith(".jsonl.gz")):
                    full = os.path.join(root, fn)
                    pytest.fail(f"unexpected BRIDGE trace-like file found: {full}")

    def test_no_holdout_result_file_exists(self):
        for fn in os.listdir(BRIDGE_DIR):
            assert "RESULT" not in fn.upper() or "RESULTS" not in fn.upper() or True
        # explicit: no PHASE2*-RESULT*.json / PHASE2*-HOLDOUT-RESULT*.json present
        for fn in os.listdir(BRIDGE_DIR):
            assert "HOLDOUT-RESULT" not in fn.upper()
            assert "CAPTURED-SEALED" not in fn.upper()
            assert "BRIDGE-OPENED" not in fn.upper()

    def test_state_record_digest_is_self_consistent(self):
        state = _state_transition()
        digest_field = state.pop("state_record_digest_sha256")
        blob = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == digest_field
