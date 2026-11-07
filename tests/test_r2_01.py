"""Tests for R2-01 (Oracle Trace Contract and Replay Validation).

Synthetic fixtures only: no real 30B model, no internet, no real Research
Part 1 historical evidence is required for these to pass. (Tests that
specifically document the real-candidate discovery are in
tests/test_r2_01_trace_source.py and are skipped if Argine2 is not
reachable as a filesystem sibling.)
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from argine.research.trace import Step, Trace, TraceMeta  # noqa: E402
from argine.research2 import runner, store  # noqa: E402
from argine.research2.r2_01 import (  # noqa: E402
    orchestrator, policy_interface, replay_contract, trace_contract, trace_source,
)


def make_trace(*, n_layers=2, n_experts=4, expert_bytes=True, with_warmup=True,
               n_decode_steps=6) -> Trace:
    """A tiny, fully synthetic, deterministic 2-layer MoE routing trace."""
    meta = TraceMeta(model="synthetic-fixture", n_experts=n_experts, top_k=2,
                     layers=list(range(n_layers)),
                     expert_bytes=({l: 1000 * (l + 1) for l in range(n_layers)}
                                  if expert_bytes else {}),
                     workload="synthetic", split="dev", source="test_r2_01.make_trace")
    steps = []
    g = 0
    if with_warmup:
        steps.append(Step(graph=g, n_tokens=1, phase="decode", segment="warmup",
                          routes={l: [(0, 1)] for l in range(n_layers)}))
        g += 1
    for i in range(n_decode_steps):
        e0 = i % n_experts
        e1 = (i + 1) % n_experts
        steps.append(Step(graph=g, n_tokens=1, phase="decode", segment="synthetic",
                          routes={l: [(e0, e1)] for l in range(n_layers)}))
        g += 1
    return Trace(meta=meta, steps=steps)


def write_trace(trace: Trace, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"format": "argine-router-trace", "version": 1,
                             "meta": trace.meta.to_json()}) + "\n")
        for st in trace.steps:
            fh.write(json.dumps(st.to_json()) + "\n")


class _FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-01-fixtures-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def trace_path(self, trace: Trace, name: str = "trace.jsonl.gz") -> Path:
        p = self.tmp / name
        write_trace(trace, p)
        return p


class NormalizationTests(_FixtureCase):
    def test_valid_trace_normalizes(self):
        trace, report = trace_contract.normalize(self.trace_path(make_trace()))
        self.assertEqual(report.source_row_count, report.normalized_step_count)
        self.assertEqual(report.excluded_row_count, 0)
        self.assertEqual(trace_contract.reconcile(report), [])

    def test_malformed_trace_rejected(self):
        p = self.tmp / "malformed.jsonl.gz"
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        with self.assertRaises(trace_contract.ContractError):
            trace_contract.normalize(p)

    def test_unknown_schema_suffix_rejected(self):
        p = self.tmp / "trace.csv"
        p.write_text("not a trace", encoding="utf-8")
        with self.assertRaises(trace_contract.ContractError):
            trace_contract.normalize(p)

    def test_missing_source_rejected(self):
        with self.assertRaises(trace_contract.ContractError):
            trace_contract.normalize(self.tmp / "does-not-exist.jsonl.gz")

    def test_invalid_expert_id_rejected(self):
        trace = make_trace(n_experts=4)
        trace.steps[0].routes[0] = [(0, 99)]  # 99 is out of range
        with self.assertRaises(trace_contract.ContractError):
            trace_contract.normalize(self.trace_path(trace))

    def test_invalid_ordering_rejected(self):
        trace = make_trace()
        trace.steps[1].graph = trace.steps[0].graph  # not strictly increasing
        with self.assertRaises(trace_contract.ContractError):
            trace_contract.normalize(self.trace_path(trace))

    def test_stable_normalized_digest(self):
        p = self.trace_path(make_trace())
        _, r1 = trace_contract.normalize(p)
        _, r2 = trace_contract.normalize(p)
        self.assertEqual(r1.normalized_trace_digest, r2.normalized_trace_digest)

    def test_preserves_routing_step_grouping(self):
        """Per-token tuples inside one step/layer must survive normalization
        unflattened (section 5's CRITICAL requirement)."""
        trace = make_trace()
        trace.steps[2].routes[0] = [(0, 1), (2, 3)]  # two tokens, same step/layer
        normalized, _ = trace_contract.normalize(self.trace_path(trace))
        self.assertEqual(normalized.steps[2].routes[0], [(0, 1), (2, 3)])
        # the union (the "event") is only derived at replay time, not stored:
        self.assertEqual(set(normalized.steps[2].experts(0)), {0, 1, 2, 3})

    def test_preserves_expert_ids(self):
        trace = make_trace(n_experts=4)
        normalized, _ = trace_contract.normalize(self.trace_path(trace))
        all_ids = {e for st in normalized.steps for sel in st.routes.values()
                  for tok in sel for e in tok}
        self.assertTrue(all_ids.issubset(set(range(4))))

    def test_no_silent_row_loss_for_empty_routes_step(self):
        trace = make_trace()
        trace.steps[3].routes = {l: [] for l in trace.meta.layers}  # selects nothing
        normalized, report = trace_contract.normalize(self.trace_path(trace))
        self.assertEqual(report.excluded_row_count, 1)
        self.assertEqual(report.exclusion_reasons, {"empty_routes_all_layers": 1})
        self.assertEqual(report.source_row_count,
                         report.normalized_step_count + report.excluded_row_count)
        self.assertEqual(trace_contract.reconcile(report), [])

    def test_source_hash_binding(self):
        p = self.trace_path(make_trace())
        _, report = trace_contract.normalize(p)
        self.assertEqual(len(report.source.sha256), 64)
        self.assertEqual(report.source.byte_size, p.stat().st_size)

    def test_layers_missing_expert_bytes_flagged(self):
        trace = make_trace(expert_bytes=False)
        _, report = trace_contract.normalize(self.trace_path(trace))
        self.assertEqual(sorted(report.layers_missing_expert_bytes), [0, 1])

    def test_cold_start_with_warmup_segment_is_documented(self):
        trace = make_trace(with_warmup=True)
        _, report = trace_contract.normalize(self.trace_path(trace))
        self.assertEqual(report.initial_residency, "cold_start_documented")

    def test_unknown_initial_state_without_warmup(self):
        trace = make_trace(with_warmup=False)
        _, report = trace_contract.normalize(self.trace_path(trace))
        self.assertEqual(report.initial_residency, "unknown_cold_assumed")


class ReplayContractTests(_FixtureCase):
    def test_deterministic_replay_digest_stable(self):
        trace, _ = trace_contract.normalize(self.trace_path(make_trace()))
        r1 = replay_contract.run_contract_replay(trace)
        r2 = replay_contract.run_contract_replay(trace)
        self.assertEqual(r1.deterministic_replay_digest, r2.deterministic_replay_digest)

    def test_capacity_enforced_no_violation_raised(self):
        trace, _ = trace_contract.normalize(self.trace_path(make_trace()))
        # should simply complete (Research Part 1's replay() already raises
        # InvariantViolation internally if a policy ever exceeds budget)
        rr = replay_contract.run_contract_replay(trace, budget=1000)
        self.assertGreaterEqual(rr.maximum_resident_bytes, 0)
        self.assertLessEqual(rr.maximum_resident_bytes, 1000)

    def test_replay_contract_error_wraps_invariant_violation(self):
        trace, _ = trace_contract.normalize(self.trace_path(make_trace()))
        import argine.research2.r2_01.replay_contract as rc

        def _boom(*a, **kw):
            from argine.research.replay import InvariantViolation
            raise InvariantViolation("synthetic invariant violation for test")

        old = rc.replay
        rc.replay = _boom
        try:
            with self.assertRaises(rc.ReplayContractError):
                rc.run_contract_replay(trace)
        finally:
            rc.replay = old


class PolicyInterfaceTests(_FixtureCase):
    def test_causal_policy_accepted(self):
        from argine.research.policies import GlobalLRU
        policy_interface.assert_causal(GlobalLRU())  # must not raise

    def test_causal_policy_cannot_access_future_events(self):
        """Structural proof: replay.replay() calls policy.access(step, layer,
        experts) once per served event, strictly in step order; nothing
        beyond the current step is ever passed in."""
        trace, _ = trace_contract.normalize(self.trace_path(make_trace()))
        seen_steps = []

        class Spy:
            needs_future = False
            name = "spy"
            family = "cache"
            category = "counterfactual"

            def __init__(self):
                self.resident_bytes = 0
                self.evictions = 0
                self.overflow_events = 0
                self.overflow_misses = 0

            def start(self, ctx):
                self.ctx = ctx

            def access(self, step, layer, experts):
                seen_steps.append(step)
                self.resident_bytes = min(self.ctx.budget, self.resident_bytes)
                return []

            def end_step(self, step):
                pass

            def allocation(self):
                return None

            def describe(self):
                return {}

        from argine.research.replay import replay as do_replay
        do_replay(trace, Spy(), budget=10_000_000, phase=None)
        self.assertEqual(seen_steps, sorted(seen_steps))  # strictly non-decreasing order
        max_seen = -1
        for s in seen_steps:
            self.assertGreaterEqual(s, max_seen)  # never jumps ahead then back
            max_seen = max(max_seen, s)

    def test_non_causal_interface_can_receive_future_information(self):
        from argine.research.oracle import BeladyGlobal
        trace, _ = trace_contract.normalize(self.trace_path(make_trace()))
        oracle_policy = BeladyGlobal(trace, phase=None)  # full trace at construction
        policy_interface.assert_non_causal(oracle_policy)

    def test_causal_rejected_as_non_causal(self):
        from argine.research.policies import GlobalLRU
        with self.assertRaises(policy_interface.PolicyInterfaceError):
            policy_interface.assert_non_causal(GlobalLRU())

    def test_non_causal_rejected_as_causal(self):
        from argine.research.oracle import BeladyGlobal
        trace, _ = trace_contract.normalize(self.trace_path(make_trace()))
        with self.assertRaises(policy_interface.PolicyInterfaceError):
            policy_interface.assert_causal(BeladyGlobal(trace, phase=None))


class TraceSourceResolverTests(_FixtureCase):
    def test_explicit_cli_source_takes_precedence(self):
        r = trace_source.resolve(explicit_source="/explicit/path",
                                 local_state_source="/local/path")
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_RESOLVED)
        self.assertEqual(r.resolved_path, "/explicit/path")
        self.assertEqual(r.tier, "cli")

    def test_local_state_source_used_when_no_explicit(self):
        r = trace_source.resolve(local_state_source="/local/path")
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_RESOLVED)
        self.assertEqual(r.resolved_path, "/local/path")
        self.assertEqual(r.tier, "local_state")

    def test_missing_when_no_candidates_anywhere(self):
        r = trace_source.resolve(argine2_root=self.tmp)  # empty dir: no candidates
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_MISSING)

    def test_missing_when_candidates_found_but_none_scientifically_eligible(self):
        """Discovery can find real files (two SMOKE runs here) while zero of
        them are scientifically eligible/plausible canonical evidence. That
        must be TRACE_SOURCE_MISSING (nothing usable), not
        TRACE_SOURCE_AMBIGUOUS -- there is no ambiguity between two runs that
        are both ineligible, and the old wording ("N plausible... 0 marked
        plausible") was internally contradictory."""
        root = self.tmp / "Argine2"
        for d in ("SMOKE-30B", "SMOKE-30B-r2"):
            p = root / "experiments" / d / "run-00-4G"
            p.mkdir(parents=True)
            write_trace(make_trace(), p / "trace.jsonl.gz")
        r = trace_source.resolve(argine2_root=root)
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_MISSING)
        self.assertEqual(len(r.candidates), 2)
        self.assertTrue(all(not c.plausible for c in r.candidates))
        # message must state the discovered count AND the zero-eligible
        # count, and must never claim any of them is plausible
        self.assertIn("2 local trace candidate(s) found", r.message)
        self.assertIn("0 scientifically eligible", r.message)

    def test_ambiguous_when_multiple_scientifically_eligible_candidates_present(self):
        """True ambiguity: two or more candidates that ARE scientifically
        eligible/plausible, with no deterministic way to pick between
        them."""
        root = self.tmp / "Argine2"
        for exp019_dir in ("windows-validation-2026-10-12", "windows-validation-2026-10-12-r2"):
            p = (root / "experiments" / "EXP-019-instrumentation-validation" / "raw"
                / exp019_dir / "run-00-4G")
            p.mkdir(parents=True)
            write_trace(make_trace(), p / "trace.jsonl.gz")
        r = trace_source.resolve(argine2_root=root)
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_AMBIGUOUS)
        self.assertEqual(len(r.candidates), 2)
        self.assertTrue(all(c.plausible for c in r.candidates))
        self.assertIn("2 scientifically eligible", r.message)

    def test_smoke_traces_remain_ineligible(self):
        root = self.tmp / "Argine2"
        p = root / "experiments" / "SMOKE-30B" / "run-00-4G"
        p.mkdir(parents=True)
        write_trace(make_trace(), p / "trace.jsonl.gz")
        r = trace_source.resolve(argine2_root=root)
        self.assertEqual(len(r.candidates), 1)
        self.assertFalse(r.candidates[0].plausible)
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_MISSING)

    def test_synthetic_fixture_remains_ineligible(self):
        root = self.tmp / "Argine2"
        p = (root / "experiments" / "EXP-019-instrumentation-validation" / "processed"
            / "e2e-fixture")
        p.mkdir(parents=True)
        write_trace(make_trace(), p / "trace.jsonl.gz")
        r = trace_source.resolve(argine2_root=root)
        self.assertEqual(len(r.candidates), 1)
        self.assertFalse(r.candidates[0].plausible)
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_MISSING)

    def test_single_plausible_candidate_resolves(self):
        root = self.tmp / "Argine2"
        p = (root / "experiments" / "EXP-019-instrumentation-validation" / "raw"
            / "windows-validation-2026-10-12-r2" / "run-00-4G")
        p.mkdir(parents=True)
        write_trace(make_trace(), p / "trace.jsonl.gz")
        r = trace_source.resolve(argine2_root=root)
        self.assertEqual(r.status, trace_source.TRACE_SOURCE_RESOLVED)
        self.assertEqual(r.tier, "documented_canonical")


class R2_01_RunnerIntegrationTests(_FixtureCase):
    def setUp(self):
        super().setUp()
        self._old_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        self.evroot = Path(tempfile.mkdtemp(prefix="r2-01-evidence-"))
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.evroot)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        if self._old_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_root
        shutil.rmtree(self.evroot, ignore_errors=True)

    def test_run_with_missing_source_creates_no_evidence(self):
        result = orchestrator.run(trace_source_arg=str(self.tmp / "nope.jsonl.gz"))
        self.assertIn(result["outcome"], (trace_source.TRACE_SOURCE_MISSING,
                                          trace_source.TRACE_SOURCE_AMBIGUOUS, "FAIL"))
        # a missing/ambiguous source must create no run directory at all
        if result["outcome"] in (trace_source.TRACE_SOURCE_MISSING,
                                 trace_source.TRACE_SOURCE_AMBIGUOUS):
            self.assertEqual(list(self.evroot.rglob("manifest.json")), [])

    def test_run_with_synthetic_source_end_to_end(self):
        p = self.trace_path(make_trace())
        result = orchestrator.run(trace_source_arg=str(p))
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        run_dir = Path(result["run_dir"])
        for name in ("manifest.json", "source-trace.json", "validation.json",
                    "summary.json", "summary.md"):
            self.assertTrue((run_dir / name).exists(), name)

    def test_analyse_after_run_returns_same_outcome(self):
        p = self.trace_path(make_trace())
        run_result = orchestrator.run(trace_source_arg=str(p))
        analyse_result = orchestrator.analyse()
        self.assertEqual(analyse_result["outcome"], run_result["outcome"])
        self.assertEqual(analyse_result["run_id"], run_result["run_id"])

    def test_analyse_with_no_runs_is_not_implemented(self):
        result = orchestrator.analyse()
        self.assertEqual(result["outcome"], "NOT_IMPLEMENTED")

    def test_runner_dispatches_to_r2_01_implementation(self):
        """runner.run_experiment is the single gated entry point (it owns the
        dirty-tree check); allow_dirty=True is passed because this test runs
        against the live development tree, which is legitimately dirty
        mid-implementation -- the dirty-tree gate itself is covered by
        test_candidate_run_refuses_dirty_tree / test_allow_dirty_marks_non_candidate
        in tests/test_research2.py."""
        p = self.trace_path(make_trace())
        result = runner.run_experiment("R2-01", trace_source_arg=str(p), allow_dirty=True)
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))

    def test_status_reflects_implemented_and_outcome_after_run(self):
        rm_before = runner.roadmap_mod.load()
        before = runner.effective_state("R2-01", rm_before, store.load_state())
        self.assertEqual(before, "IMPLEMENTED")

        p = self.trace_path(make_trace())
        # allow_dirty=True: see test_runner_dispatches_to_r2_01_implementation.
        runner.run_experiment("R2-01", trace_source_arg=str(p), allow_dirty=True)

        rm_after = runner.roadmap_mod.load()
        after = runner.effective_state("R2-01", rm_after, store.load_state())
        self.assertIn(after, ("COMPLETED", "INCONCLUSIVE", "FAILED"))

    def test_r2_02_not_eligible_after_non_completed_r2_01(self):
        p = self.trace_path(make_trace(with_warmup=False))  # -> INCONCLUSIVE, not PASS
        result = orchestrator.run(trace_source_arg=str(p))
        self.assertNotEqual(result["outcome"], "PASS")
        rm = runner.roadmap_mod.load()
        nxt = runner.next_eligible(rm, store.load_state())
        self.assertEqual(nxt["experiment_id"], "R2-01")  # NOT R2-02

    def test_allow_dirty_marks_non_candidate_for_r2_01(self):
        """Fulfils the 'dirty-tree refusal + --allow-dirty => NON-CANDIDATE'
        requirement specifically for R2-01's real implementation (the
        generic stub version of this is tests/test_research2.py::
        test_allow_dirty_marks_non_candidate, now exercised against R2-03
        since R2-02 is also a real implementation -- see tests/
        test_r2_02_*.py for its own non-candidate coverage)."""
        p = self.trace_path(make_trace())
        result = runner.run_experiment("R2-01", trace_source_arg=str(p), allow_dirty=True)
        self.assertEqual(result.get("candidate_status"), "NON-CANDIDATE")


class M01RegressionStillGreenTests(unittest.TestCase):
    def test_m01_focused_suite_still_passes(self):
        env = dict(os.environ)
        env["ARGINE3_RESEARCH_ROOT"] = tempfile.mkdtemp(prefix="r2-01-m01-check-")
        try:
            r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_research2"],
                              cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        finally:
            shutil.rmtree(env["ARGINE3_RESEARCH_ROOT"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
