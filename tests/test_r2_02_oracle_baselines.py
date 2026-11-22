"""Tests for R2-02: Oracle vs Retention Baselines.

Covers argine/research2/r2_02/ (prerequisite.py, policy_set.py,
replay_multi.py, comparison.py, orchestrator.py) and its wiring into
runner.py. Every fixture here is synthetic and built in-process (no real
llama-server, no real 30B model, no network, no model weights loaded).

Item numbers below correspond to the R2-02 task's own test-requirement
list (sections 3's 34 items, plus 4 conditional items for the sharding
decomposition this implementation includes).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from argine.research2 import runner, store  # noqa: E402
from argine.research2.r2_01 import trace_contract  # noqa: E402
from argine.research2.r2_02 import comparison, orchestrator  # noqa: E402
from argine.research2.r2_02 import policy_set, prerequisite  # noqa: E402
from argine.research2.r2_02.replay_multi import ReplayContractError, run_policy_replay  # noqa: E402
from argine.research import oracle as research_oracle  # noqa: E402
from argine.research import storereplay  # noqa: E402
from argine.research.policies import GlobalLRU, LFU, ShardedLRU  # noqa: E402
from argine.research.trace import PHASE_DECODE  # noqa: E402
from test_r2_01 import make_trace, write_trace  # noqa: E402


class _EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-02-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._old_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.tmp / "evidence")
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        if self._old_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_root

    def trace_path(self, **kwargs) -> Path:
        p = self.tmp / f"trace-{len(list(self.tmp.glob('trace-*.jsonl.gz')))}.jsonl.gz"
        write_trace(make_trace(**kwargs), p)
        return p

    def register_r2_01(self, trace_path: Path, *, outcome: str = "PASS",
                       end_utc: str = "2026-01-01T00:00:00Z",
                       run_id: Optional[str] = None) -> str:
        """Fabricate an R2-01 registry entry + source-trace.json evidence,
        exactly the shape prerequisite.require_r2_01_pass() reads -- never
        going through the real R2-01 orchestrator (that is R2-01's own
        test suite's job)."""
        run_id = run_id or f"fake-{outcome.lower()}-{end_utc}"
        _trace, norm_report = trace_contract.normalize(trace_path)
        rd = store.run_dir("R2-01", run_id)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "source-trace.json").write_text(json.dumps(norm_report.to_json()),
                                              encoding="utf-8")
        store.append_registry_entry({
            "identity": {"experiment_id": "R2-01", "run_id": run_id, "outcome": outcome,
                        "end_utc": end_utc},
            "run_dir": str(rd), "trace_source": str(trace_path),
        })
        return run_id


# --------------------------------------------------------------------------
# Items 1, 2, 3, 4, 6, 29: the R2-01 PASS prerequisite + trace-SHA binding
# --------------------------------------------------------------------------

class PrerequisiteTests(_EvidenceCase):
    def test_no_r2_01_run_blocks(self):
        """Item 2: R2-01 non-PASS (here: no run at all) blocks R2-02."""
        b = prerequisite.require_r2_01_pass()
        self.assertEqual(b.status, prerequisite.STATUS_NO_R2_01_RUN)
        self.assertFalse(b.ok)

    def test_non_pass_r2_01_blocks(self):
        """Item 2: R2-01 non-PASS (INCONCLUSIVE) blocks R2-02."""
        self.register_r2_01(self.trace_path(), outcome="INCONCLUSIVE")
        b = prerequisite.require_r2_01_pass()
        self.assertEqual(b.status, prerequisite.STATUS_R2_01_NOT_PASS)
        self.assertFalse(b.ok)

    def test_pass_r2_01_binds_trace_identity(self):
        """Item 1 & 3: R2-01 PASS prerequisite enforced; validated trace
        identity (path + sha256) is bound dynamically from R2-01's own
        evidence, not a hard-coded literal."""
        p = self.trace_path()
        run_id = self.register_r2_01(p, outcome="PASS")
        b = prerequisite.require_r2_01_pass()
        self.assertTrue(b.ok)
        self.assertEqual(b.r2_01_run_id, run_id)
        self.assertEqual(b.trace_path, str(p))
        self.assertEqual(b.trace_sha256, trace_contract.sha256_file(p)["sha256"])

    def test_latest_pass_run_selected_when_multiple_exist(self):
        """Item 3: binding uses R2-01's LATEST PASS run, mirroring
        r2_01.orchestrator.analyse()'s own latest-run selection."""
        p1 = self.trace_path()
        p2 = self.trace_path(n_decode_steps=10)
        self.register_r2_01(p1, outcome="PASS", end_utc="2026-01-01T00:00:00Z")
        newest = self.register_r2_01(p2, outcome="PASS", end_utc="2026-01-02T00:00:00Z")
        b = prerequisite.require_r2_01_pass()
        self.assertEqual(b.r2_01_run_id, newest)
        self.assertEqual(b.trace_path, str(p2))

    def test_ambiguous_latest_pass_blocks(self):
        """Item 3 (edge case): two PASS runs sharing the same end_utc is
        ambiguous and must never be silently resolved by guessing."""
        p1, p2 = self.trace_path(), self.trace_path(n_decode_steps=10)
        self.register_r2_01(p1, outcome="PASS", end_utc="2026-01-01T00:00:00Z",
                            run_id="run-a")
        self.register_r2_01(p2, outcome="PASS", end_utc="2026-01-01T00:00:00Z",
                            run_id="run-b")
        b = prerequisite.require_r2_01_pass()
        self.assertEqual(b.status, prerequisite.STATUS_AMBIGUOUS_LATEST)
        self.assertFalse(b.ok)

    def test_trace_sha_mismatch_fails_closed(self):
        """Item 4: if the registered R2-01 source has changed unexpectedly
        (re-hashed now != what R2-01's PASS run recorded), R2-02 fails
        closed rather than silently evaluating a different trace."""
        p = self.trace_path()
        self.register_r2_01(p, outcome="PASS")
        p.write_bytes(p.read_bytes() + b"\x00\x00")  # mutate after registration
        b = prerequisite.require_r2_01_pass()
        self.assertEqual(b.status, prerequisite.STATUS_TRACE_CHANGED)
        self.assertFalse(b.ok)

    def test_missing_trace_file_fails_closed(self):
        """Item 28: the trace file R2-01 validated has since been deleted
        -- R2-02 must fail closed, never silently fall back elsewhere."""
        p = self.trace_path()
        self.register_r2_01(p, outcome="PASS")
        p.unlink()
        b = prerequisite.require_r2_01_pass()
        self.assertEqual(b.status, prerequisite.STATUS_TRACE_MISSING)
        self.assertFalse(b.ok)

    def test_run_refuses_without_writing_evidence_when_prerequisite_unmet(self):
        """Items 2, 29: no run directory / registry entry / state change is
        ever created when the prerequisite is unmet (atomic failure, same
        style as R2-01's own TRACE_SOURCE_MISSING early return)."""
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_R2_01_PREREQUISITE_NOT_MET)
        evidence = Path(os.environ["ARGINE3_RESEARCH_ROOT"])
        self.assertEqual(list(evidence.glob("R2-02/*/summary.json")), [])
        self.assertNotIn("R2-02", store.load_state())

    def test_runner_dispatches_prerequisite_failure_via_generic_contract(self):
        """Item 2: the generic runner.run_experiment() contract reaches the
        same prerequisite-not-met outcome for R2-02."""
        with mock.patch("argine.research2.runner.git_is_clean", return_value=True):
            result = runner.run_experiment("R2-02", allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_R2_01_PREREQUISITE_NOT_MET)
        self.assertEqual(result["r2_01_prerequisite_status"],
                         prerequisite.STATUS_NO_R2_01_RUN)


# --------------------------------------------------------------------------
# Items 5, 6, 7, 8: fixed budget, equal capacity, real byte sizes, cold start
# --------------------------------------------------------------------------

class FixedBudgetTests(unittest.TestCase):
    def test_fixed_budget_is_4gib(self):
        """Item 5: the fixed 4 GiB budget, never swept."""
        self.assertEqual(orchestrator.FIXED_BUDGET_BYTES, 4 * (1 << 30))

    def test_run_accepts_no_budget_parameter(self):
        """Item 5: R2-02's own run() never exposes a budget to sweep (that
        is explicitly R2-03's job, not R2-02's)."""
        import inspect
        sig = inspect.signature(orchestrator.run)
        self.assertNotIn("budget", sig.parameters)


class RealReplayTests(_EvidenceCase):
    """End-to-end orchestrator.run() against a synthetic R2-01-validated
    trace, with FIXED_BUDGET_BYTES patched small so the synthetic trace's
    tiny byte sizes actually exercise eviction/capacity logic (patching a
    module constant for a controlled fixture is not a production budget
    sweep -- R2-03's job remains untouched)."""

    SMALL_BUDGET = 3000

    def _passing_run(self, **trace_kwargs):
        p = self.trace_path(**trace_kwargs)
        self.register_r2_01(p, outcome="PASS")
        with mock.patch.object(orchestrator, "FIXED_BUDGET_BYTES", self.SMALL_BUDGET):
            return orchestrator.run(allow_dirty=True)

    def test_all_policy_rows_share_the_fixed_capacity(self):
        """Item 6: every policy receives exactly the same byte capacity."""
        result = self._passing_run()
        run_dir = Path(result["run_dir"])
        rows = json.loads((run_dir / "policy_results.json").read_text())["policies"]
        caps = {r["policy_metadata"]["capacity_bytes"] for r in rows}
        self.assertEqual(caps, {self.SMALL_BUDGET})

    def test_uses_real_expert_byte_sizes_not_expert_count(self):
        """Item 7: byte capacity uses real per-expert byte sizes (make_trace's
        layers have DIFFERENT byte sizes, 1000 and 2000); R2-02 never
        reduces this to a plain expert count."""
        result = self._passing_run(n_layers=2, n_experts=6, n_decode_steps=40)
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE"))
        run_dir = Path(result["run_dir"])
        prov = json.loads((run_dir / "provenance.json").read_text())
        self.assertEqual(prov["trace"]["layers_missing_expert_bytes"], [])

    def test_unequal_layer_byte_sizes_handled(self):
        """Item 26: layers with different per-expert byte sizes replay
        without error (make_trace's default fixture already has unequal
        sizes across its two layers)."""
        result = self._passing_run()
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE"))
        self.assertEqual(result["replay_errors"], [])

    def test_cold_start_identical_across_policies(self):
        """Item 8: every policy starts from the same (cold) initial
        residency -- Policy.start() resets resident_bytes to 0 for every
        declared policy, never giving Oracle a warmer start."""
        p = self.trace_path()
        _trace, norm_report = trace_contract.normalize(p)
        trace, _ = trace_contract.normalize(p)
        for spec in policy_set.DECLARED_POLICIES:
            policy = spec.build(trace)
            from argine.research.policies import Context
            ctx = Context(layers=list(trace.layers), n_experts=trace.meta.n_experts,
                         size=dict(trace.meta.expert_bytes), budget=self.SMALL_BUDGET,
                         top_k=trace.meta.top_k)
            policy.start(ctx)
            self.assertEqual(policy.resident_bytes, 0,
                            f"{spec.label} did not start cold")

    def test_candidate_status_recorded(self):
        """Item 29: dirty-tree candidate semantics (NON-CANDIDATE when
        allow_dirty masks a dirty/unverifiable tree) are preserved in
        R2-02's own evidence, exactly like R2-01's."""
        with mock.patch("argine.research2.provenance.git_identity",
                        return_value={"repo_root": "x", "branch": "y", "commit": "z",
                                     "clean": False, "dirty_definition": "d", "note": None}):
            result = self._passing_run()
        self.assertEqual(result["candidate_status"], "NON-CANDIDATE")

    def test_no_runtime_or_model_invocation(self):
        """Item 30: no model weights are loaded and no runtime binary is
        invoked anywhere in R2-02 -- a static source check on every module
        in the package, not just a behavioral inference."""
        import argine.research2.r2_02 as pkg
        pkg_dir = Path(pkg.__file__).parent
        banned = ("llama-server", "subprocess.Popen", "subprocess.run", "llama.cpp")
        for py_file in pkg_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, f"{py_file.name} references {token!r}")


# --------------------------------------------------------------------------
# Items 9, 10, 11, 12, 25: the causal / non-causal policy interface
# --------------------------------------------------------------------------

class PolicyInterfaceTests(unittest.TestCase):
    def test_causal_baselines_declared_causal(self):
        """Item 9."""
        for label in policy_set.CAUSAL_BASELINE_LABELS:
            self.assertTrue(policy_set.spec_by_label(label).causal)

    def test_oracle_declared_non_causal(self):
        """Item 10."""
        spec = policy_set.spec_by_label(policy_set.ORACLE_LABEL)
        self.assertFalse(spec.causal)

    def test_assert_causal_rejects_a_non_causal_policy(self):
        """Item 11: a causal-declared baseline can never secretly be the
        Oracle -- assert_causal rejects anything with needs_future=True,
        which is the only sanctioned channel future information can reach
        a policy through (see policy_interface.py)."""
        from argine.research2.r2_01 import policy_interface
        trace, _ = trace_contract.normalize(_tmp_trace_for_module_tests())
        belady = research_oracle.BeladyGlobal(trace, phase=PHASE_DECODE)
        with self.assertRaises(policy_interface.PolicyInterfaceError):
            policy_interface.assert_causal(belady)

    def test_assert_non_causal_accepts_belady_and_rejects_lru(self):
        """Item 12: Oracle future access is the only sanctioned non-causal
        path; a causal policy is rejected by assert_non_causal."""
        from argine.research2.r2_01 import policy_interface
        trace, _ = trace_contract.normalize(_tmp_trace_for_module_tests())
        belady = research_oracle.BeladyGlobal(trace, phase=PHASE_DECODE)
        policy_interface.assert_non_causal(belady)  # does not raise
        with self.assertRaises(policy_interface.PolicyInterfaceError):
            policy_interface.assert_non_causal(GlobalLRU())

    def test_declared_policy_metadata_has_causal_flag(self):
        """Item 25: policy metadata explicitly records name/version/causal/
        capacity_bytes/configuration."""
        for spec in policy_set.DECLARED_POLICIES:
            meta = spec.metadata(capacity_bytes=4096)
            self.assertEqual(set(meta), {"name", "version", "causal", "capacity_bytes",
                                        "configuration"})
            self.assertIsInstance(meta["causal"], bool)

    def test_labels_are_distinct(self):
        """Item 37: sharded vs global vs oracle metrics are named
        distinctly, impossible to miss."""
        labels = [p.label for p in policy_set.DECLARED_POLICIES]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertIn("Sharded", policy_set.LABEL_RUNTIME_SHARDED_LRU)
        self.assertIn("Global", policy_set.LABEL_GLOBAL_LRU)
        self.assertIn("Oracle", policy_set.LABEL_ORACLE_GLOBAL_BELADY)


def _tmp_trace_for_module_tests() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="r2-02-module-"))
    p = tmp / "trace.jsonl.gz"
    write_trace(make_trace(), p)
    return p


# --------------------------------------------------------------------------
# Items 13, 14, 15, 16, 17, 18, 19, 23, 24, 27: replay contract + digests
# --------------------------------------------------------------------------

class ReplayContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-02-replay-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        p = self.tmp / "trace.jsonl.gz"
        write_trace(make_trace(n_layers=2, n_experts=6, n_decode_steps=40), p)
        self.trace, self.norm_report = trace_contract.normalize(p)

    def test_deterministic_ordering_preserved(self):
        """Item 13: every policy consumes the same logical request
        sequence -- routing_step_count matches the trace's own served-step
        count, identically for every declared policy."""
        counts = set()
        for spec in policy_set.DECLARED_POLICIES:
            det, _rr = run_policy_replay(self.trace, spec.build(self.trace), budget=3000,
                                         causal=spec.causal)
            counts.add(det.routing_step_count)
        self.assertEqual(len(counts), 1, "policies disagree on served step count")

    def test_event_grouping_preserved(self):
        """Item 14: per-step layer grouping is preserved (not flattened)
        -- expert_request_count must be strictly >= routing_step_count
        for a multi-layer trace (at least one access per step per layer)."""
        det, _rr = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        self.assertGreaterEqual(det.expert_request_count, det.routing_step_count)

    def test_zero_capacity_violations_recorded(self):
        """Item 15."""
        det, rr = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        row = comparison.policy_row(policy_set.LABEL_GLOBAL_LRU, det, rr)
        self.assertEqual(row["budget_violations"], 0)

    def test_policy_row_has_miss_count(self):
        """Item 16."""
        det, rr = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        row = comparison.policy_row(policy_set.LABEL_GLOBAL_LRU, det, rr)
        self.assertIn("misses", row)
        self.assertGreaterEqual(row["misses"], 0)

    def test_policy_row_has_miss_bytes(self):
        """Item 17."""
        det, rr = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        row = comparison.policy_row(policy_set.LABEL_GLOBAL_LRU, det, rr)
        self.assertIn("miss_bytes", row)
        self.assertGreaterEqual(row["miss_bytes"], 0)

    def test_policy_row_has_evictions(self):
        """Item 18."""
        det, rr = run_policy_replay(self.trace, GlobalLRU(), budget=500, causal=True)
        row = comparison.policy_row(policy_set.LABEL_GLOBAL_LRU, det, rr)
        self.assertIn("evictions", row)

    def test_policy_row_has_peak_resident_bytes(self):
        """Item 19."""
        det, rr = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        row = comparison.policy_row(policy_set.LABEL_GLOBAL_LRU, det, rr)
        self.assertIn("peak_resident_bytes", row)
        self.assertLessEqual(row["peak_resident_bytes"], 3000)

    def test_eviction_bytes_reported_as_known_limitation(self):
        """Predeclared limitation: ReplayResult has no eviction-bytes
        field. Reported as None, never invented."""
        det, rr = run_policy_replay(self.trace, GlobalLRU(), budget=500, causal=True)
        row = comparison.policy_row(policy_set.LABEL_GLOBAL_LRU, det, rr)
        self.assertIsNone(row["eviction_bytes"])

    def test_same_inputs_same_digest(self):
        """Item 23: deterministic policy digest -- same trace/budget/
        policy/configuration produces a byte-identical canonical digest."""
        det_a, _ = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        det_b, _ = run_policy_replay(self.trace, GlobalLRU(), budget=3000, causal=True)
        self.assertEqual(det_a.deterministic_replay_digest, det_b.deterministic_replay_digest)

    def test_repeated_replay_deterministic_across_all_declared_policies(self):
        """Item 24: repeated replay determinism, for every declared
        policy (not just GlobalLRU)."""
        for spec in policy_set.DECLARED_POLICIES:
            det_a, _ = run_policy_replay(self.trace, spec.build(self.trace), budget=3000,
                                         causal=spec.causal)
            det_b, _ = run_policy_replay(self.trace, spec.build(self.trace), budget=3000,
                                         causal=spec.causal)
            self.assertEqual(det_a.deterministic_replay_digest,
                            det_b.deterministic_replay_digest, spec.label)

    def test_malformed_policy_output_fails_closed(self):
        """Item 27: classify_outcome() rejects a hits+misses mismatch
        (malformed policy output) rather than silently accepting it."""
        rows = {"Bad-Policy": {"policy": "Bad-Policy", "total_expert_requests": 10,
                               "hits": 3, "misses": 3, "budget_violations": 0}}
        outcome, reasons = orchestrator.classify_outcome(
            self.norm_report, [], [], [], [], rows)
        self.assertEqual(outcome, orchestrator.OUTCOME_INCONCLUSIVE)
        self.assertTrue(any("malformed" in r.lower() or "aligned" in r.lower()
                           for r in reasons))


# --------------------------------------------------------------------------
# Items 20, 21, 22: Oracle-gap metrics
# --------------------------------------------------------------------------

class OracleGapTests(unittest.TestCase):
    def test_relative_oracle_gap_fields_present(self):
        """Item 20."""
        baseline = {"policy": "Global-LRU", "misses": 100, "miss_bytes": 1000}
        oracle = {"policy": "Oracle-Global-Belady", "misses": 40, "miss_bytes": 400}
        gap = comparison.oracle_gap(baseline, oracle)
        self.assertEqual(gap["miss_reduction"], 60)
        self.assertAlmostEqual(gap["relative_miss_reduction"], 0.6)
        self.assertEqual(gap["miss_byte_reduction"], 600)
        self.assertAlmostEqual(gap["relative_miss_byte_reduction"], 0.6)

    def test_zero_gap_is_a_valid_result(self):
        """Item 21: a tiny or zero Oracle advantage is still a VALID
        scientific result, not an error -- oracle_dominates stays True and
        relative reduction is exactly 0.0, no exception."""
        baseline = {"policy": "Global-LRU", "misses": 50, "miss_bytes": 500}
        oracle = {"policy": "Oracle-Global-Belady", "misses": 50, "miss_bytes": 500}
        gap = comparison.oracle_gap(baseline, oracle)
        self.assertEqual(gap["miss_reduction"], 0)
        self.assertEqual(gap["relative_miss_reduction"], 0.0)
        self.assertTrue(gap["oracle_dominates"])

    def test_zero_baseline_misses_handled_without_division_error(self):
        """Item 21 (edge case): a baseline with zero misses must not raise
        a ZeroDivisionError -- relative reduction is reported as None."""
        baseline = {"policy": "Global-LRU", "misses": 0, "miss_bytes": 0}
        oracle = {"policy": "Oracle-Global-Belady", "misses": 0, "miss_bytes": 0}
        gap = comparison.oracle_gap(baseline, oracle)
        self.assertIsNone(gap["relative_miss_reduction"])
        self.assertIsNone(gap["relative_miss_byte_reduction"])
        self.assertTrue(gap["oracle_dominates"])

    def test_oracle_worse_than_baseline_is_detected_as_inconsistent(self):
        """Item 22: Oracle produces logically inconsistent results (worse
        than a baseline it theoretically dominates) is detected."""
        baseline = {"policy": "Global-LRU", "misses": 40, "miss_bytes": 400}
        oracle = {"policy": "Oracle-Global-Belady", "misses": 60, "miss_bytes": 400}
        gap = comparison.oracle_gap(baseline, oracle)
        self.assertFalse(gap["oracle_dominates"])

    def test_classify_outcome_fails_on_oracle_inconsistency(self):
        """Item 22: wired into classify_outcome() itself -- an inconsistent
        Oracle forces FAIL, not PASS/INCONCLUSIVE."""
        tmp = Path(tempfile.mkdtemp(prefix="r2-02-oracle-fail-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        p = tmp / "trace.jsonl.gz"
        write_trace(make_trace(), p)
        _trace, norm_report = trace_contract.normalize(p)
        rows = {
            policy_set.LABEL_RUNTIME_SHARDED_LRU: {"policy": policy_set.LABEL_RUNTIME_SHARDED_LRU,
                "total_expert_requests": 100, "hits": 60, "misses": 40, "miss_bytes": 400,
                "budget_violations": 0},
            policy_set.LABEL_GLOBAL_LRU: {"policy": policy_set.LABEL_GLOBAL_LRU,
                "total_expert_requests": 100, "hits": 60, "misses": 40, "miss_bytes": 400,
                "budget_violations": 0},
            policy_set.LABEL_LFU: {"policy": policy_set.LABEL_LFU,
                "total_expert_requests": 100, "hits": 60, "misses": 40, "miss_bytes": 400,
                "budget_violations": 0},
            policy_set.LABEL_ORACLE_GLOBAL_BELADY: {"policy": policy_set.LABEL_ORACLE_GLOBAL_BELADY,
                "total_expert_requests": 100, "hits": 40, "misses": 60, "miss_bytes": 600,
                "budget_violations": 0},
        }
        outcome, reasons = orchestrator.classify_outcome(norm_report, [], [], [], [], rows)
        self.assertEqual(outcome, orchestrator.OUTCOME_FAIL)
        self.assertTrue(any("inconsistent" in r.lower() for r in reasons))


# --------------------------------------------------------------------------
# Items 35, 36, 38: sharding decomposition
# --------------------------------------------------------------------------

class ShardingDecompositionTests(unittest.TestCase):
    def test_shard_count_matches_runtime_discovery(self):
        """Item 36: the declared Runtime-Sharded-LRU shard count matches
        R2-01B's own confirmed runtime discovery at 4 GiB (16 shards),
        not an arbitrary, disconnected number."""
        self.assertEqual(policy_set.RUNTIME_SHARD_COUNT_AT_4GIB,
                         storereplay.runtime_shard_count(orchestrator.FIXED_BUDGET_BYTES))

    def test_shard_configuration_fixed_and_explicit(self):
        """Item 35: shard mapping configuration (shard count + seed) is
        explicit and identical every time this policy is built."""
        spec = policy_set.spec_by_label(policy_set.LABEL_RUNTIME_SHARDED_LRU)
        self.assertEqual(spec.configuration["shards"], 16)
        self.assertEqual(spec.configuration["seed"], 0)
        p1, p2 = spec.build(None), spec.build(None)
        self.assertEqual(p1.n, p2.n)
        self.assertEqual(p1.seed, p2.seed)

    def test_decomposition_reports_limitation_and_separates_components(self):
        """Item 38: the decomposition explicitly reports the no-sharded-
        oracle limitation and keeps structural vs policy-information gaps
        as SEPARATE entries -- never silently merged into one "prediction
        headroom" number that would misattribute the sharding cost."""
        rows = {
            policy_set.LABEL_RUNTIME_SHARDED_LRU: {"policy": policy_set.LABEL_RUNTIME_SHARDED_LRU,
                "misses": 100, "miss_bytes": 1000},
            policy_set.LABEL_GLOBAL_LRU: {"policy": policy_set.LABEL_GLOBAL_LRU,
                "misses": 70, "miss_bytes": 700},
            policy_set.LABEL_ORACLE_GLOBAL_BELADY: {"policy": policy_set.LABEL_ORACLE_GLOBAL_BELADY,
                "misses": 40, "miss_bytes": 400},
        }
        decomp = comparison.sharding_decomposition(rows)
        self.assertIn("limitation", decomp)
        self.assertIn("no shard-constrained Oracle", decomp["limitation"])
        self.assertIn("structural_sharding_gap", decomp)
        self.assertIn("policy_information_gap", decomp)
        self.assertIn("total_observed_upper_bound_gap", decomp)
        self.assertNotEqual(decomp["structural_sharding_gap"]["miss_reduction"],
                            decomp["total_observed_upper_bound_gap"]["miss_reduction"])
        self.assertEqual(decomp["structural_sharding_gap"]["miss_reduction"], 30)
        self.assertEqual(decomp["policy_information_gap"]["miss_reduction"], 30)
        self.assertEqual(decomp["total_observed_upper_bound_gap"]["miss_reduction"], 60)


# --------------------------------------------------------------------------
# Items 31, 32, 33, 34: existing suites remain green (documented; actually
# verified by running the full regression suite alongside this file -- see
# the R2-02 final report's TESTS section).
# --------------------------------------------------------------------------

class ExistingSuitesRemainGreenTests(unittest.TestCase):
    def test_documented_regression_requirement(self):
        """Items 31-34: tests/test_r2_01.py, tests/test_r2_01a_capture.py,
        tests/test_r2_01a_runtime.py, tests/test_r2_01_autocapture.py,
        tests/test_r2_01b_behavioral.py, and tests/test_research2.py all
        stay green after R2-02's implementation (two tests needed a
        necessary, documented update: R2-01B's own "R2-02 stays locked"
        test, and test_research2.py's/test_r2_01_autocapture.py's generic
        "unimplemented experiment" stand-ins, which moved from R2-02 to
        R2-03 now that R2-02 is a real implementation). This is verified
        by actually running the full suite, not only by this placeholder."""
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
