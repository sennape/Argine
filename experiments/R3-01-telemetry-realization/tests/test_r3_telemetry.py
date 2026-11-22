"""R3-01 tests: telemetry record realization, pairing and admission.

Standard library only. No model, no acquisition, no C++.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

PHASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PHASE_DIR))

import r3_telemetry.telemetry as T  # noqa: E402

R2_RUNTIME_DIR = T.REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "runtime_results"
CONFORMANCE_TRACE = (T.REPO_ROOT / "experiments" / "R2-12-runtime-validation"
                     / "conformance_traces" / "conformance-b_selected-20261115.jsonl")


class TestRecords(unittest.TestCase):
    def test_trace_count_counters_are_exact(self):
        rec = T.fixture_trace_count()
        c = rec["telemetry"]["counters"]
        self.assertEqual(c["expert_miss_bytes"], 1000)
        self.assertEqual(c["expert_bytes_read"], 1000)
        self.assertEqual(c["expert_miss_count"], 7)
        self.assertEqual(c["expert_read_count"], 21)          # DERIVED: 3 x misses
        self.assertEqual(c["expert_hits"], 3)
        self.assertEqual(c["expert_evictions"], 1)
        self.assertEqual(c["requested_expert_events"], 10)
        self.assertEqual(c["hit_expert_events"] + c["miss_expert_events"], 10)

    def test_read_count_is_derived_not_exact(self):
        rec = T.fixture_trace_count()
        self.assertEqual(rec["provenance"]["exact_vs_derived"]["expert_read_count"],
                         "DERIVED_RUNTIME (3 x expert misses)")

    def test_trace_count_record_is_admissible(self):
        out = T.admit_record(T.fixture_trace_count())
        self.assertEqual(out["status"], "ADMISSIBLE", out["failures"])

    def test_timing_record_is_admissible(self):
        out = T.admit_record(T.fixture_timing())
        self.assertEqual(out["status"], "ADMISSIBLE", out["failures"])

    def test_cell_pair_is_admissible(self):
        out = T.admit_cell(T.fixture_trace_count(), T.fixture_timing())
        self.assertEqual(out["status"], "ADMISSIBLE", out["failures"])

    def test_trace_archive_is_reference_only(self):
        rec = T.fixture_trace_count()
        a = rec["telemetry"]["archive"]
        self.assertFalse(a["copied_into_repository"])
        self.assertEqual(len(a["sha256"]), 64)
        self.assertGreater(a["bytes"], 0)
        self.assertTrue(a["path"].endswith("fixture-trace.jsonl"))

    def test_record_serialization_is_deterministic(self):
        r1 = T.fixture_trace_count()
        r2 = T.fixture_trace_count()
        self.assertEqual(T.canonical_json(r1), T.canonical_json(r2))
        self.assertEqual(T.digest_of(r1), T.digest_of(r2))
        with tempfile.TemporaryDirectory() as d:
            p1, p2 = Path(d) / "a.json", Path(d) / "a2.json"
            T.write_json_deterministic(p1, r1)
            T.write_json_deterministic(p2, r2)
            self.assertEqual(p1.read_bytes(), p2.read_bytes())


class TestAdmissionFailsClosed(unittest.TestCase):
    def _cell(self, trace=None, timing=None):
        return T.admit_cell(trace or T.fixture_trace_count(), timing or T.fixture_timing())

    def assertInadmissible(self, out, rule):
        self.assertEqual(out["status"], "INADMISSIBLE")
        self.assertIn(rule, out["failures"])

    def test_missing_trace_count_telemetry(self):
        rec = copy.deepcopy(T.fixture_trace_count())
        del rec["telemetry"]["counters"]["expert_miss_bytes"]
        self.assertInadmissible(T.admit_record(rec), "V1_REQUIRED_FIELDS")

    def test_missing_timing_telemetry(self):
        rec = copy.deepcopy(T.fixture_timing())
        del rec["timing"]["ttft_seconds"]
        self.assertInadmissible(T.admit_record(rec), "V1_REQUIRED_FIELDS")

    def test_mismatched_workload(self):
        self.assertInadmissible(self._cell(timing=T.fixture_timing(workload_id="other")), "C1_SAME_CELL")

    def test_mismatched_seed(self):
        self.assertInadmissible(self._cell(timing=T.fixture_timing(seed=7)), "C1_SAME_CELL")

    def test_mismatched_budget(self):
        self.assertInadmissible(self._cell(timing=T.fixture_timing(budget_bytes=8 * 1024 ** 3)),
                                "C1_SAME_CELL")

    def test_mismatched_policy_params(self):
        params = dict(T.FIXTURE_PARAMS, decay_factor=0.5)
        ident = T.fixture_identity()
        ident["policy_params_sha256"] = T.policy_params_digest(params)
        timing = T.fixture_timing(policy_params=params, identity=ident)
        self.assertInadmissible(self._cell(timing=timing), "C1_SAME_CELL")

    def test_mismatched_output_digest(self):
        self.assertInadmissible(self._cell(timing=T.fixture_timing(output_tokens=[9, 9])),
                                "C2_OUTPUT_IDENTITY")

    def test_incomplete_trace(self):
        rec = T.fixture_trace_count(max_lines=2)
        self.assertInadmissible(T.admit_record(rec), "V5_COMPLETENESS")

    def test_budget_violation(self):
        rec = copy.deepcopy(T.fixture_trace_count())
        rec["telemetry"]["counters"]["resident_bytes_peak"] = 4 * 1024 ** 3 + 1
        self.assertInadmissible(T.admit_record(rec), "V8_BUDGET_INVARIANT")

    def test_non_hex_identity(self):
        rec = T.fixture_trace_count()
        rec["identity"]["runtime_sha256"] = "zz"
        self.assertInadmissible(T.admit_record(rec), "V3_IDENTITIES_HEX64")

    def test_altered_contract_digest(self):
        rec = T.fixture_trace_count()
        rec["contract_digest"] = "0" * 64
        self.assertInadmissible(T.admit_record(rec), "X1_CONTRACT_DIGEST")

    def test_wrong_schema_version(self):
        rec = T.fixture_trace_count()
        rec["telemetry_schema_version"] = 99
        self.assertInadmissible(T.admit_record(rec), "X2_SCHEMA_VERSION")

    def test_seed_missing_blocks_cell(self):
        trace = T.fixture_trace_count(seed=None)
        timing = T.fixture_timing(seed=None)
        self.assertInadmissible(T.admit_cell(trace, timing), "X3_SEED_PRESENT")

    def test_counter_invariant_violation(self):
        rec = copy.deepcopy(T.fixture_trace_count())
        rec["telemetry"]["counters"]["expert_read_count"] = 99999
        self.assertInadmissible(T.admit_record(rec), "X7_COUNTER_INVARIANTS")

    def test_trace_budget_mismatch(self):
        rec = copy.deepcopy(T.fixture_trace_count())
        rec["budget_bytes"] = 8 * 1024 ** 3
        self.assertInadmissible(T.admit_record(rec), "X6_TRACE_BUDGET")

    def test_canonical_r2_record_is_inadmissible(self):
        rec = json.loads(next(R2_RUNTIME_DIR.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(T.admit_record(rec)["status"], "INADMISSIBLE")


class TestOutputTokenDigest(unittest.TestCase):
    def test_order_sensitive(self):
        self.assertNotEqual(T.output_token_digest([1, 2, 3]), T.output_token_digest([3, 2, 1]))

    def test_container_independent(self):
        self.assertEqual(T.output_token_digest([1, 2, 3]), T.output_token_digest(iter([1, 2, 3])))

    def test_logging_mode_independent(self):
        tokens = [101, 7, 9]
        logged = json.loads(json.dumps({"tokens": tokens}))["tokens"]
        self.assertEqual(T.output_token_digest(tokens), T.output_token_digest(logged))

    def test_empty_sequence_is_defined(self):
        self.assertEqual(len(T.output_token_digest([])), 64)

    def test_rejects_non_integer_tokens(self):
        with self.assertRaises(ValueError):
            T.output_token_digest(["a"])


class TestRealTraceFixture(unittest.TestCase):
    def test_bounded_extraction(self):
        if not CONFORMANCE_TRACE.exists():
            self.skipTest("conformance trace not present")
        rec = T.fixture_trace_count()  # ensure builder works with fixture trace
        self.assertEqual(rec["telemetry"]["status"], "COMPLETE")

    def test_real_trace_tail_is_completion(self):
        if not CONFORMANCE_TRACE.exists():
            self.skipTest("conformance trace not present")
        with CONFORMANCE_TRACE.open("rb") as fh:
            fh.seek(max(0, CONFORMANCE_TRACE.stat().st_size - 4000))
            tail = fh.read().decode("utf-8", "replace")
        last = [l for l in tail.splitlines() if l.strip()][-1]
        self.assertIn("complete", json.loads(last))


class TestFrozenInputs(unittest.TestCase):
    def test_contract_digest_matches_frozen_value(self):
        self.assertEqual(T.digest_of(T.CONTRACT), T.CONTRACT_DIGEST)

    def test_contract_file_digest_matches_r3_00a_freeze(self):
        freeze = json.loads((T.R3A_DIR / "FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(freeze["contract"]["sha256"], T.file_sha256(T.CONTRACT_PATH))

    def test_audit_file_matches_r3_00a_freeze(self):
        freeze = json.loads((T.R3A_DIR / "FREEZE.json").read_text(encoding="utf-8"))
        audit = T.R3A_DIR / "CANONICAL_R2_12_AUDIT.json"
        self.assertEqual(freeze["audit"]["sha256"], T.file_sha256(audit))


if __name__ == "__main__":
    unittest.main(verbosity=2)
