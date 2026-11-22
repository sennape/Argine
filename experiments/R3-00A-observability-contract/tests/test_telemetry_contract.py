"""Tests for the R3-00A observability contract.

Standard library only (``python -m unittest``), no model, no trace acquisition.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PHASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PHASE_DIR / "validator"))

import telemetry_contract as tc  # noqa: E402


def _valid_identity(seed: str) -> dict:
    return {
        "model_sha256": (seed * 64)[:64],
        "store_sha256": (seed * 64)[:64],
        "runtime_sha256": (seed * 64)[:64],
        "workload_sha256": (seed * 64)[:64],
        "policy_params_sha256": (seed * 64)[:64],
    }


def _count_record(**over) -> dict:
    rec = {
        "workload_id": "w1",
        "seed": 42,
        "budget_bytes": 4 * 1024**3,
        "policy_id": "b_selected",
        "output_token_digest": "a" * 64,
        "identity": _valid_identity("a"),
        "telemetry": {
            "mode": "TRACE_COUNT",
            "status": "COMPLETE",
            "trace_sha256": "b" * 64,
            "counters": {
                "expert_bytes_read": 1000,
                "expert_read_count": 30,
                "expert_miss_bytes": 1000,
                "expert_miss_count": 10,
                "expert_hits": 5,
                "expert_evictions": 2,
                "resident_bytes_peak": 4 * 1024**3,
            },
        },
    }
    rec.update(over)
    return rec


def _timing_record(**over) -> dict:
    rec = {
        "workload_id": "w1",
        "seed": 42,
        "budget_bytes": 4 * 1024**3,
        "policy_id": "b_selected",
        "output_token_digest": "a" * 64,
        "identity": _valid_identity("a"),
        "telemetry": {
            "mode": "TIMING",
            "status": "COMPLETE",
            "overhead": {"events_served": 1234, "process_cpu_seconds": 90.0},
        },
        "timing": {
            "ttft_seconds": 10.0,
            "decode_duration_seconds": 120.0,
            "tokens_generated": 512,
            "process_cpu_seconds": 900.0,
        },
    }
    rec.update(over)
    return rec


class TestCanonicalSet(unittest.TestCase):
    def test_canonical_set_is_120_and_40_per_budget(self):
        sel = tc.select_canonical_runs()
        self.assertEqual(len(sel["canonical"]), 120)
        per = {}
        for _, d in sel["canonical"]:
            per[d["budget_gib"]] = per.get(d["budget_gib"], 0) + 1
        self.assertEqual(per, {1.5: 40, 4.0: 40, 8.0: 40})

    def test_launch_split_is_120_counted_and_24_warmups(self):
        sel = tc.select_canonical_runs()
        self.assertEqual(len(sel["canonical"]), 120)
        self.assertEqual(len(sel["warmups"]), 24)
        self.assertEqual(sel["top_level_files"], 161)
        self.assertEqual(sel["launch_records"], 144)
        self.assertEqual(len(sel["control_files"]), 17)

    def test_three_precanonical_attempts_explain_123(self):
        sel = tc.select_canonical_runs()
        self.assertEqual(len(sel["precanonical_counted"]), 3)
        self.assertEqual(len(sel["selftest_counted"]), 1)
        self.assertEqual(len(sel["canonical"]) + len(sel["precanonical_counted"]), 123)
        for p, d in sel["precanonical_counted"]:
            self.assertIn("PRECANONICAL_TRACE_ENABLED_ATTEMPTS", str(p))
            self.assertEqual(d["budget_gib"], 4.0)
            self.assertTrue(d["counted"])
            self.assertFalse(d["warmup"])

    def test_canonical_runs_have_no_trace_and_no_telemetry(self):
        audit = tc.build_canonical_audit()
        self.assertEqual(len(audit["canonical_runs"]), 120)
        for r in audit["canonical_runs"]:
            self.assertFalse(r["trace_enabled"])
            self.assertEqual(r["telemetry_status"], "ABSENT")
        self.assertEqual(audit["telemetry_completeness"]["admissible_under_r3_contract"], 0)
        for field, n in audit["telemetry_completeness"]["per_field_non_null"].items():
            self.assertEqual(n, 0, field)

    def test_audit_digest_is_deterministic(self):
        a1 = tc.build_canonical_audit()
        a2 = tc.build_canonical_audit()
        self.assertEqual(a1["audit_digest"], a2["audit_digest"])


class TestAdmissibility(unittest.TestCase):
    def test_complete_count_run_is_admitted(self):
        out = tc.validate_run(_count_record(), "TRACE_COUNT")
        self.assertTrue(out["admitted"], out["reasons"])

    def test_r2_12_record_fails_closed(self):
        sel = tc.select_canonical_runs()
        p, d = sel["canonical"][0]
        out = tc.validate_run(d, "TRACE_COUNT")
        self.assertFalse(out["admitted"])
        self.assertIn("V1_required_fields", " ".join(out["reasons"]))

    def test_missing_counters_fail(self):
        rec = _count_record()
        rec["telemetry"]["status"] = None
        self.assertFalse(tc.validate_run(rec, "TRACE_COUNT")["admitted"])

    def test_budget_violation_fails(self):
        rec = _count_record()
        rec["telemetry"]["counters"]["resident_bytes_peak"] = 4 * 1024**3 + 1
        out = tc.validate_run(rec, "TRACE_COUNT")
        self.assertFalse(out["admitted"])
        self.assertTrue(any(r.startswith("V8_budget_invariant") for r in out["reasons"]))

    def test_miss_bytes_larger_than_read_fails(self):
        rec = _count_record()
        rec["telemetry"]["counters"]["expert_miss_bytes"] = 1001
        self.assertFalse(tc.validate_run(rec, "TRACE_COUNT")["admitted"])

    def test_miss_count_larger_than_read_count_fails(self):
        rec = _count_record()
        rec["telemetry"]["counters"]["expert_miss_count"] = 31
        self.assertFalse(tc.validate_run(rec, "TRACE_COUNT")["admitted"])

    def test_non_hex_identity_fails(self):
        rec = _count_record()
        rec["identity"]["runtime_sha256"] = "not-a-digest"
        self.assertFalse(tc.validate_run(rec, "TRACE_COUNT")["admitted"])

    def test_incomplete_trace_fails(self):
        rec = _count_record()
        rec["telemetry"]["status"] = "INCOMPLETE"
        self.assertFalse(tc.validate_run(rec, "TRACE_COUNT")["admitted"])

    def test_wrong_mode_fails(self):
        self.assertFalse(tc.validate_run(_count_record(), "TIMING")["admitted"])

    def test_timing_requires_positive_timings(self):
        rec = _timing_record()
        rec["timing"]["tokens_generated"] = 0
        self.assertFalse(tc.validate_run(rec, "TIMING")["admitted"])

    def test_cell_pair_admitted(self):
        out = tc.validate_cell(_count_record(), _timing_record())
        self.assertTrue(out["admitted"], out["reasons"])

    def test_cell_pair_rejects_divergent_output(self):
        timing = _timing_record()
        timing["output_token_digest"] = "c" * 64
        out = tc.validate_cell(_count_record(), timing)
        self.assertFalse(out["admitted"])
        self.assertTrue(any("C2_output_identity" in r for r in out["reasons"]))

    def test_cell_pair_rejects_different_params(self):
        timing = _timing_record()
        timing["identity"] = _valid_identity("b")
        out = tc.validate_cell(_count_record(), timing)
        self.assertFalse(out["admitted"])
        self.assertTrue(any("C1_same_cell" in r for r in out["reasons"]))


class TestDecisionTraceExtractor(unittest.TestCase):
    def _write(self, rows) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        for r in rows:
            tmp.write(json.dumps(r) + "\n")
        tmp.close()
        return Path(tmp.name)

    def test_exact_counters_on_synthetic_trace(self):
        rows = [
            {"step": 0, "policy": "b_selected", "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [1, 2, 3], "hit_experts": [1], "miss_experts": [2, 3],
             "evicted": [], "cache_resident_bytes_after": 400, "budget_bytes": 1000},
            {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 400},
            {"step": 1, "policy": "b_selected", "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [2, 3], "hit_experts": [2, 3], "miss_experts": [],
             "evicted": [1], "cache_resident_bytes_after": 400, "budget_bytes": 1000},
            {"step": 1, "complete": 1, "cache_resident_bytes_post_service": 400},
        ]
        c = tc.extract_decision_trace(self._write(rows))
        self.assertEqual(c["miss_expert_events"], 2)
        self.assertEqual(c["miss_bytes"], 200)
        self.assertEqual(c["hit_expert_events"], 3)
        self.assertEqual(c["evicted_expert_events"], 1)
        self.assertEqual(c["requested_expert_events"], 5)
        self.assertEqual(c["steps_completed"], 2)
        self.assertTrue(c["complete"])
        self.assertEqual(c["budget_violations"], 0)

    def test_truncated_trace_is_not_complete(self):
        rows = [
            {"step": 0, "policy": "b_selected", "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [1], "hit_experts": [], "miss_experts": [1],
             "evicted": [], "cache_resident_bytes_after": 100, "budget_bytes": 1000},
        ]
        c = tc.extract_decision_trace(self._write(rows))
        self.assertFalse(c["complete"])

    def test_budget_violation_is_not_complete(self):
        rows = [
            {"step": 0, "policy": "b_selected", "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [1], "hit_experts": [], "miss_experts": [1],
             "evicted": [], "cache_resident_bytes_after": 2000, "budget_bytes": 1000},
            {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 2000},
        ]
        c = tc.extract_decision_trace(self._write(rows))
        self.assertEqual(c["budget_violations"], 1)
        self.assertFalse(c["complete"])

    def test_real_trace_ends_with_completion_row(self):
        trace = (tc.REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "conformance_traces"
                 / "conformance-b_selected-20261115.jsonl")
        if not trace.exists():
            self.skipTest("conformance trace not present")
        with trace.open("rb") as fh:
            fh.seek(max(0, trace.stat().st_size - 4000))
            tail = fh.read().decode("utf-8", "replace")
        lines = [l for l in tail.splitlines() if l.strip()]
        self.assertTrue(lines)
        last = json.loads(lines[-1])
        self.assertIn("complete", last)


class TestContractDocument(unittest.TestCase):
    def test_contract_lists_all_required_fields(self):
        contract = tc.load_contract()
        names = {f["name"] for f in contract["fields"]}
        for required in (
            "expert_bytes_read", "expert_read_count", "expert_miss_bytes",
            "per_read_issue_timestamp", "per_read_completion_timestamp",
            "per_read_wait_seconds", "queue_depth", "resident_bytes", "evictions",
            "requested_experts", "hit_miss_experts", "policy_decision_wall_time",
            "policy_bookkeeping_wall_time", "protected_set_construction_wall_time",
            "decode_step_wall_time", "compute_io_overlap",
        ):
            self.assertIn(required, names)

    def test_classes_are_from_the_allowed_vocabulary(self):
        allowed = {"EXACT_RUNTIME", "DERIVED_RUNTIME", "MODELED", "ANALYSIS_ONLY", "UNAVAILABLE"}
        contract = tc.load_contract()
        for f in contract["fields"]:
            self.assertIn(f["class"], allowed, f["name"])

    def test_contract_digest_is_deterministic(self):
        c = tc.load_contract()
        self.assertEqual(tc.contract_digest(c), tc.contract_digest(c))
        # key order must not matter
        reordered = dict(reversed(list(c.items())))
        self.assertEqual(tc.contract_digest(c), tc.contract_digest(reordered))

    def test_not_frozen_list_keeps_endpoint_out(self):
        contract = tc.load_contract()
        self.assertTrue(any("endpoint" in x for x in contract["not_frozen"]))
        self.assertTrue(any("policy" in x for x in contract["not_frozen"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
