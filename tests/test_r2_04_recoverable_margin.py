"""Tests for R2-04: Recoverable-Margin Analysis.

Covers argine/research2/r2_04/ (prerequisite.py, decomposition.py,
regimes.py, lfu_summary.py, claims.py, figures.py, orchestrator.py) and
its wiring into runner.py. Every fixture here is synthetic and built
in-process (no real llama-server, no real 30B model, no network, no
model weights loaded, no new trace capture) -- per the task's own "do NOT
execute against real canonical evidence during development" instruction.
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
from argine.research2.r2_02 import orchestrator as r2_02_orchestrator  # noqa: E402
from argine.research2.r2_02 import comparison as r2_02_comparison  # noqa: E402
from argine.research2.r2_02 import policy_set  # noqa: E402
from argine.research2.r2_03 import budget_sweep  # noqa: E402
from argine.research2.r2_03 import orchestrator as r2_03_orchestrator  # noqa: E402
from argine.research2.r2_04 import (claims, decomposition, figures, lfu_summary,
                                    orchestrator, prerequisite, regimes)  # noqa: E402
from test_r2_01 import make_trace, write_trace  # noqa: E402

LABEL_SHARDED = policy_set.LABEL_RUNTIME_SHARDED_LRU
LABEL_GLOBAL_LRU = policy_set.LABEL_GLOBAL_LRU
LABEL_LFU = policy_set.LABEL_LFU
LABEL_ORACLE = policy_set.ORACLE_LABEL


def _row(policy: str, misses: int, miss_bytes: int, evictions: int = 0,
        total_expert_requests: int = 1000, hits: Optional[int] = None) -> dict:
    hits = hits if hits is not None else (total_expert_requests - misses)
    return {"policy": policy, "misses": misses, "miss_bytes": miss_bytes,
           "evictions": evictions, "hits": hits,
           "total_expert_requests": total_expert_requests,
           "miss_rate": misses / total_expert_requests if total_expert_requests else 0.0}


# ==========================================================================
# Direct unit tests: decomposition.py (items: core formulas, relative-
# fraction non-additivity, negative structural gap, missing-policy /
# impossible-value errors)
# ==========================================================================

class DecompositionTests(unittest.TestCase):
    def test_structural_information_total_sum_exactly_absolute(self):
        """Items: absolute miss-byte and miss-count decomposition."""
        rows = {LABEL_SHARDED: _row(LABEL_SHARDED, 100, 100000),
               LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
               LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)}
        d = decomposition.decompose_budget(rows)
        s, i, t = (d[decomposition.KEY_STRUCTURAL], d[decomposition.KEY_INFORMATION],
                  d[decomposition.KEY_TOTAL])
        self.assertEqual(s["miss_reduction"] + i["miss_reduction"], t["miss_reduction"])
        self.assertEqual(s["miss_byte_reduction"] + i["miss_byte_reduction"],
                         t["miss_byte_reduction"])
        self.assertEqual(t["miss_reduction"], 60)
        self.assertEqual(t["miss_byte_reduction"], 60000)

    def test_relative_fractions_use_distinct_denominators_and_are_not_summed(self):
        """Item: section 8 -- never force relative fractions to add."""
        rows = {LABEL_SHARDED: _row(LABEL_SHARDED, 100, 100000),
               LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
               LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)}
        d = decomposition.decompose_budget(rows)
        structural_rel = d[decomposition.KEY_STRUCTURAL]["relative_miss_byte_reduction"]
        info_rel = d[decomposition.KEY_INFORMATION]["relative_miss_byte_reduction"]
        total_rel = d[decomposition.KEY_TOTAL]["relative_miss_byte_reduction"]
        self.assertAlmostEqual(structural_rel, (100000 - 90000) / 100000)
        self.assertAlmostEqual(info_rel, (90000 - 40000) / 90000)
        self.assertAlmostEqual(total_rel, (100000 - 40000) / 100000)
        self.assertNotAlmostEqual(structural_rel + info_rel, total_rel)

    def test_negative_structural_gap_is_not_an_error(self):
        """Item: sharding itself reducing misses relative to Global-LRU
        (observed at the real evidence's 1.5/2 GiB points) must be a
        valid result, not raise."""
        rows = {LABEL_SHARDED: _row(LABEL_SHARDED, 80, 80000),
               LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
               LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)}
        d = decomposition.decompose_budget(rows)
        self.assertEqual(d[decomposition.KEY_STRUCTURAL]["miss_reduction"], -10)
        self.assertLess(d[decomposition.KEY_STRUCTURAL]["relative_miss_byte_reduction"], 0)
        # identity still holds with a negative structural component
        s, i, t = (d[decomposition.KEY_STRUCTURAL], d[decomposition.KEY_INFORMATION],
                  d[decomposition.KEY_TOTAL])
        self.assertEqual(s["miss_byte_reduction"] + i["miss_byte_reduction"],
                         t["miss_byte_reduction"])

    def test_zero_gap_is_not_an_error(self):
        """Item: a zero/tiny headroom result is a VALID scientific
        result (section 18), never an error."""
        rows = {LABEL_SHARDED: _row(LABEL_SHARDED, 10, 10000),
               LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 10, 10000),
               LABEL_ORACLE: _row(LABEL_ORACLE, 10, 10000)}
        d = decomposition.decompose_budget(rows)
        self.assertEqual(d[decomposition.KEY_TOTAL]["miss_byte_reduction"], 0)
        self.assertEqual(d[decomposition.KEY_TOTAL]["relative_miss_byte_reduction"], 0.0)

    def test_missing_policy_raises_missing_policy_error(self):
        rows = {LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
               LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)}
        with self.assertRaises(decomposition.MissingPolicyError):
            decomposition.decompose_budget(rows)

    def test_decompose_all_budgets_skips_missing_policy_budgets(self):
        budget_results = {
            1000: {LABEL_SHARDED: _row(LABEL_SHARDED, 100, 100000),
                  LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
                  LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)},
            2000: {LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000)},  # missing 2 policies
        }
        out = decomposition.decompose_all_budgets(budget_results)
        self.assertEqual(set(out), {1000})

    def test_impossible_values_raise_and_propagate(self):
        """Item: a mathematically impossible comparison must FAIL, never
        be silently skipped by decompose_all_budgets."""
        # Hand-craft rows whose oracle_gap() results cannot satisfy the
        # additive identity by directly forging a row dict that violates
        # it is not possible through oracle_gap() itself (it's always
        # exact by construction); instead verify decompose_all_budgets
        # does NOT catch ImpossibleValuesError by monkeypatching
        # decompose_budget to raise it.
        with mock.patch.object(decomposition, "decompose_budget",
                              side_effect=decomposition.ImpossibleValuesError("boom")):
            with self.assertRaises(decomposition.ImpossibleValuesError):
                decomposition.decompose_all_budgets({1000: {}})


# ==========================================================================
# Direct unit tests: regimes.py
# ==========================================================================

class RegimesTests(unittest.TestCase):
    def _budget_results(self):
        return {
            1000: {LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000, evictions=5),
                  LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000, evictions=5)},
            2000: {LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 50, 50000, evictions=2),
                  LABEL_ORACLE: _row(LABEL_ORACLE, 50, 50000, evictions=0)},
            3000: {LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 10, 10000, evictions=0),
                  LABEL_ORACLE: _row(LABEL_ORACLE, 10, 10000, evictions=0)},
        }

    def test_capacity_constrained_budgets(self):
        """Item: Global-LRU evictions > 0 defines capacity pressure."""
        out = regimes.capacity_constrained_budgets(self._budget_results(), LABEL_GLOBAL_LRU)
        self.assertEqual(out, [1000, 2000])

    def test_effective_residency_is_first_tested_convergence_not_any_tie(self):
        """Item: a mere miss-count tie with nonzero evictions (budget
        2000, Oracle evictions=0 but Global-LRU evictions=2) does NOT
        count; only budget 3000 (both zero evictions) qualifies, and it
        is reported as the FIRST tested budget exhibiting it."""
        out = regimes.effective_residency_budget(self._budget_results(), LABEL_GLOBAL_LRU, LABEL_ORACLE)
        self.assertEqual(out, 3000)

    def test_no_effective_residency_when_none_converge(self):
        br = {1000: {LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000, evictions=5),
                    LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000, evictions=5)}}
        self.assertIsNone(regimes.effective_residency_budget(br, LABEL_GLOBAL_LRU, LABEL_ORACLE))

    def test_peak_opportunity_maxima_can_differ_by_budget(self):
        """Item: section 10 -- the four maxima are determined
        independently, never assumed to coincide."""
        decomposition_by_budget = {
            1000: {decomposition.KEY_INFORMATION: {
                "relative_miss_byte_reduction": 0.9, "miss_byte_reduction": 10,
                "relative_miss_reduction": 0.1, "miss_reduction": 1}},
            2000: {decomposition.KEY_INFORMATION: {
                "relative_miss_byte_reduction": 0.1, "miss_byte_reduction": 1000,
                "relative_miss_reduction": 0.9, "miss_reduction": 500}},
        }
        peak = regimes.peak_opportunity(decomposition_by_budget)
        self.assertEqual(peak["max_relative_miss_byte_headroom"]["budget_bytes"], 1000)
        self.assertEqual(peak["max_absolute_miss_byte_headroom"]["budget_bytes"], 2000)
        self.assertEqual(peak["max_relative_miss_headroom"]["budget_bytes"], 2000)
        self.assertEqual(peak["max_absolute_miss_headroom"]["budget_bytes"], 2000)

    def test_headroom_persistence_excludes_effective_residency_point(self):
        """Item: section 14 -- the effective-residency point is excluded
        from the persistence question."""
        decomposition_by_budget = {
            1000: {decomposition.KEY_INFORMATION: {"miss_byte_reduction": 50000, "miss_reduction": 50}},
            3000: {decomposition.KEY_INFORMATION: {"miss_byte_reduction": 0, "miss_reduction": 0}},
        }
        out = regimes.headroom_persistence(decomposition_by_budget, [1000, 3000], 3000)
        self.assertEqual(out["capacity_constrained_budgets_checked"], [1000])
        self.assertTrue(out["oracle_strictly_improves_at_every_capacity_constrained_point"])

    def test_headroom_persistence_detects_violation(self):
        decomposition_by_budget = {
            1000: {decomposition.KEY_INFORMATION: {"miss_byte_reduction": 0, "miss_reduction": 0}},
        }
        out = regimes.headroom_persistence(decomposition_by_budget, [1000], None)
        self.assertFalse(out["oracle_strictly_improves_at_every_capacity_constrained_point"])
        self.assertEqual(len(out["violations"]), 1)

    def _curve(self, values, budgets=None):
        budgets = budgets or list(range(1000, 1000 * (len(values) + 1), 1000))
        return [{"budget_bytes": b, "relative_miss_byte_reduction": v} for b, v in zip(budgets, values)]

    def test_curve_monotonic_increasing(self):
        out = regimes.classify_curve_shape(self._curve([0.1, 0.2, 0.3, 0.4]), None)
        self.assertEqual(out["classification"], regimes.CURVE_MONOTONIC_INCREASING)

    def test_curve_monotonic_decreasing(self):
        out = regimes.classify_curve_shape(self._curve([0.4, 0.3, 0.2, 0.1]), None)
        self.assertEqual(out["classification"], regimes.CURVE_MONOTONIC_DECREASING)

    def test_curve_plateau(self):
        out = regimes.classify_curve_shape(self._curve([0.3, 0.3, 0.3, 0.3]), None)
        self.assertEqual(out["classification"], regimes.CURVE_PLATEAU)

    def test_curve_single_interior_maximum(self):
        out = regimes.classify_curve_shape(self._curve([0.1, 0.5, 0.2]), None)
        self.assertEqual(out["classification"], regimes.CURVE_SINGLE_INTERIOR_MAXIMUM)

    def test_curve_multiple_local_maxima(self):
        out = regimes.classify_curve_shape(self._curve([0.1, 0.5, 0.1, 0.6, 0.1]), None)
        self.assertEqual(out["classification"], regimes.CURVE_MULTIPLE_LOCAL_MAXIMA)

    def test_curve_collapse_at_effective_residency(self):
        budgets = [1000, 2000, 3000, 4000]
        out = regimes.classify_curve_shape(self._curve([0.2, 0.4, 0.7, 0.0], budgets), 4000)
        self.assertEqual(out["classification"], regimes.CURVE_COLLAPSE_AT_EFFECTIVE_RESIDENCY)

    def test_curve_not_forced_to_collapse_label_without_effective_residency(self):
        """Item: no regime is assumed ahead of time -- the same shape
        without a matching effective-residency budget must not be
        labelled a collapse."""
        budgets = [1000, 2000, 3000, 4000]
        out = regimes.classify_curve_shape(self._curve([0.2, 0.4, 0.7, 0.0], budgets), None)
        self.assertNotEqual(out["classification"], regimes.CURVE_COLLAPSE_AT_EFFECTIVE_RESIDENCY)


# ==========================================================================
# Direct unit tests: lfu_summary.py (item: LFU stays secondary only)
# ==========================================================================

class LfuSummaryTests(unittest.TestCase):
    def test_lfu_better_worse_equal_verdicts(self):
        br = {
            1000: {LABEL_LFU: _row(LABEL_LFU, 10, 10000), LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 20, 20000)},
            2000: {LABEL_LFU: _row(LABEL_LFU, 20, 20000), LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 10, 10000)},
            3000: {LABEL_LFU: _row(LABEL_LFU, 10, 10000), LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 10, 10000)},
        }
        out = lfu_summary.lfu_vs_global_lru(br, LABEL_GLOBAL_LRU)
        verdicts = {row["budget_bytes"]: row["verdict"] for row in out}
        self.assertEqual(verdicts, {1000: "LFU_BETTER", 2000: "LFU_WORSE", 3000: "LFU_EQUAL"})


# ==========================================================================
# Direct unit tests: claims.py (items: SUPPORTED_NOW/NOT_YET_SUPPORTED
# split, naming safeguard, documented-not-computed II-C future metric)
# ==========================================================================

class ClaimsTests(unittest.TestCase):
    def _decomposition_by_budget(self):
        rows_1000 = {LABEL_SHARDED: _row(LABEL_SHARDED, 100, 100000),
                    LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
                    LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)}
        return {1000: decomposition.decompose_budget(rows_1000)}

    def test_supported_and_not_yet_supported_both_present(self):
        claim_set = claims.build_claims(self._decomposition_by_budget(), 1000, None,
                                        "monotonic_increasing")
        self.assertTrue(claim_set["supported_now"])
        self.assertTrue(claim_set["not_yet_supported"])
        self.assertEqual(claim_set["causal_recovery"], "NOT_YET_MEASURED -- II-C")

    def test_future_metric_documented_never_computed(self):
        self.assertIn("formula", claims.FUTURE_METRIC_II_C)
        self.assertIn("NOT_YET_SUPPORTED", claims.FUTURE_METRIC_II_C["status"])
        # the formula string names variables, not a numeric value
        self.assertNotIn("=", claims.FUTURE_METRIC_II_C["formula"].split("=", 1)[1].strip()[:1])

    def test_forbidden_phrase_in_supported_claim_raises(self):
        claim_set = {"supported_now": [{"claim": "A predictor can be recovered fully.",
                                       "basis": "x"}],
                    "not_yet_supported": []}
        with self.assertRaises(claims.ForbiddenClaimError):
            claims.check_no_forbidden_claims(claim_set)

    def test_naming_safeguard_phrasing_in_generated_claims(self):
        """Item: section 5 -- SUPPORTED NOW information-headroom claims
        use 'upper bound' / 'headroom' phrasing."""
        claim_set = claims.build_claims(self._decomposition_by_budget(), 1000, None,
                                        "monotonic_increasing")
        claims.check_no_forbidden_claims(claim_set)  # must not raise
        joined = " ".join(c["claim"] for c in claim_set["supported_now"])
        self.assertIn("headroom", joined.lower())


# ==========================================================================
# Direct unit tests: figures.py
# ==========================================================================

class FiguresTests(unittest.TestCase):
    def test_figure_a_b_c_shapes(self):
        rows_1000 = {LABEL_SHARDED: _row(LABEL_SHARDED, 100, 100000),
                    LABEL_GLOBAL_LRU: _row(LABEL_GLOBAL_LRU, 90, 90000),
                    LABEL_ORACLE: _row(LABEL_ORACLE, 40, 40000)}
        budget_results = {1000: rows_1000}
        decomposition_by_budget = {1000: decomposition.decompose_budget(rows_1000)}

        fig_a = figures.figure_a_miss_rate_by_budget(budget_results, LABEL_GLOBAL_LRU, LABEL_ORACLE)
        self.assertEqual(fig_a[0]["budget_bytes"], 1000)
        self.assertIn("global_lru_miss_rate", fig_a[0])
        self.assertIn("oracle_miss_rate", fig_a[0])

        fig_b = figures.figure_b_information_headroom_fraction_by_budget(decomposition_by_budget)
        self.assertIn("relative_miss_byte_reduction", fig_b[0])

        fig_c = figures.figure_c_structural_vs_informational_absolute_gap_by_budget(decomposition_by_budget)
        self.assertEqual(fig_c[0]["structural_miss_byte_gap"] + fig_c[0]["informational_miss_byte_gap"],
                         fig_c[0]["total_miss_byte_gap"])


# ==========================================================================
# Prerequisite chain tests (fail-closed; synthetic registry entries)
# ==========================================================================

class _EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-04-"))
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
        run_id = run_id or f"fake-r201-{outcome.lower()}-{end_utc}"
        _trace, norm_report = trace_contract.normalize(trace_path)
        rd = store.run_dir("R2-01", run_id)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "source-trace.json").write_text(json.dumps(norm_report.to_json()), encoding="utf-8")
        store.append_registry_entry({
            "identity": {"experiment_id": "R2-01", "run_id": run_id, "outcome": outcome,
                        "end_utc": end_utc},
            "run_dir": str(rd), "trace_source": str(trace_path)})
        return run_id

    def run_real_r2_02(self, *, budget: int = 6000) -> dict:
        with mock.patch.object(r2_02_orchestrator, "FIXED_BUDGET_BYTES", budget):
            return r2_02_orchestrator.run(allow_dirty=True)

    SWEEP = (3000, 4500, 6000, 12000, 24000)
    REPRO_BUDGET = 6000

    def run_real_r2_03(self) -> dict:
        with mock.patch.object(budget_sweep, "FIXED_BUDGETS_BYTES", self.SWEEP), \
             mock.patch.object(budget_sweep, "REPRODUCTION_BUDGET_BYTES", self.REPRO_BUDGET):
            return r2_03_orchestrator.run(allow_dirty=True)

    def build_full_passing_chain(self, **trace_kwargs) -> dict:
        """Builds a genuine R2-01 -> R2-02 -> R2-03 PASS chain (all real
        orchestrators, synthetic small-budget fixture) that R2-04 can
        then synthesize over."""
        p = self.trace_path(n_layers=2, n_experts=6, n_decode_steps=40, **trace_kwargs)
        self.register_r2_01(p, outcome="PASS")
        r202 = self.run_real_r2_02(budget=self.REPRO_BUDGET)
        self.assertEqual(r202["outcome"], "PASS")
        r203 = self.run_real_r2_03()
        self.assertEqual(r203["outcome"], "PASS", r203.get("reasons"))
        return r203


class PrerequisiteTests(_EvidenceCase):
    def test_no_r2_03_run_blocks(self):
        """Item 1/2/3 analogue for R2-04: R2-01/R2-02 OK but no R2-03 run
        at all blocks R2-04."""
        p = self.trace_path()
        self.register_r2_01(p, outcome="PASS")
        self.run_real_r2_02()
        b = prerequisite.require_r2_01_r2_02_r2_03_pass()
        self.assertEqual(b.status, prerequisite.STATUS_NO_R2_03_RUN)
        self.assertFalse(b.ok)

    def test_r2_01_or_r2_02_prerequisite_failure_propagates(self):
        b = prerequisite.require_r2_01_r2_02_r2_03_pass()
        self.assertEqual(b.status, prerequisite.STATUS_R2_01_OR_R2_02_PREREQUISITE_FAILED)
        self.assertFalse(b.ok)

    def test_r2_03_not_pass_blocks(self):
        p = self.trace_path()
        self.register_r2_01(p, outcome="PASS")
        self.run_real_r2_02()
        rd = store.run_dir("R2-03", "fake-r203-inconclusive")
        rd.mkdir(parents=True, exist_ok=True)
        store.append_registry_entry({
            "identity": {"experiment_id": "R2-03", "run_id": "fake-r203-inconclusive",
                        "outcome": "INCONCLUSIVE", "end_utc": "2026-01-03T00:00:00Z"},
            "run_dir": str(rd)})
        b = prerequisite.require_r2_01_r2_02_r2_03_pass()
        self.assertEqual(b.status, prerequisite.STATUS_R2_03_NOT_PASS)
        self.assertFalse(b.ok)

    def test_ok_binding_on_real_chain(self):
        """Item: binding resolves fixed_budgets_bytes/reproduction_budget_bytes
        from R2-03's OWN registered evidence, not today's module
        constants (verified equal here only because the fixture used the
        same values; the point is the SOURCE, exercised in the mismatch
        test below)."""
        self.build_full_passing_chain()
        b = prerequisite.require_r2_01_r2_02_r2_03_pass()
        self.assertTrue(b.ok, b.message)
        self.assertEqual(tuple(b.fixed_budgets_bytes), self.SWEEP)
        self.assertEqual(b.reproduction_budget_bytes, self.REPRO_BUDGET)

    def test_r2_03_binding_mismatch_fails_closed(self):
        """Item: R2-03's own recorded binding disagreeing with the
        CURRENT R2-01/R2-02 binding refuses the run. Isolates R2-04's own
        check (as opposed to r2_03.prerequisite's upstream check, which
        would itself already fail if R2-01/R2-02 were inconsistent) by
        leaving R2-01/R2-02 untouched and corrupting only R2-03's own
        registered provenance.json binding."""
        r203 = self.build_full_passing_chain()
        run_dir = Path(r203["run_dir"])
        provenance_path = run_dir / "provenance.json"
        prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        prov["binding"]["trace_sha256"] = "0" * 64
        provenance_path.write_text(json.dumps(prov), encoding="utf-8")
        b = prerequisite.require_r2_01_r2_02_r2_03_pass()
        self.assertEqual(b.status, prerequisite.STATUS_BINDING_MISMATCH)
        self.assertFalse(b.ok)

    def test_r2_03_reproduction_invalid_blocks(self):
        """Item: R2-03's own registered reproduction_check.ok == False
        (or missing) blocks R2-04, even if R2-03's outcome was otherwise
        PASS (defends against a corrupted/hand-edited evidence file)."""
        r203 = self.build_full_passing_chain()
        run_dir = Path(r203["run_dir"])
        comparison_path = run_dir / "comparison.json"
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        comparison["reproduction_check"]["ok"] = False
        comparison_path.write_text(json.dumps(comparison), encoding="utf-8")
        b = prerequisite.require_r2_01_r2_02_r2_03_pass()
        self.assertEqual(b.status, prerequisite.STATUS_R2_03_REPRODUCTION_INVALID)
        self.assertFalse(b.ok)


# ==========================================================================
# End-to-end orchestrator tests
# ==========================================================================

class EndToEndTests(_EvidenceCase):
    def test_real_chain_end_to_end_pass(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_PASS, result.get("reasons"))
        self.assertEqual(result["synthesis_errors"], [])
        self.assertEqual(result["nondeterminism"], [])
        self.assertEqual(result["summary_metrics"]["causal_recovery"], "NOT_YET_MEASURED -- II-C")

        run_dir = Path(result["run_dir"])
        for fname in ("manifest.json", "provenance.json", "recoverable_margin.json",
                     "claims.json", "margin_by_budget.csv", "summary.json", "summary.md"):
            self.assertTrue((run_dir / fname).is_file(), fname)

        margin = json.loads((run_dir / "recoverable_margin.json").read_text())
        self.assertEqual(set(int(b) for b in margin["decomposition_by_budget"]), set(self.SWEEP))
        self.assertEqual(margin["effective_residency_budget"], 24000)
        self.assertEqual(sorted(margin["capacity_constrained_budgets"]), [3000, 4500, 6000, 12000])

        claims_doc = json.loads((run_dir / "claims.json").read_text())
        self.assertIn("supported_now", claims_doc)
        self.assertIn("not_yet_supported", claims_doc)
        self.assertEqual(claims_doc["causal_recovery"], "NOT_YET_MEASURED -- II-C")

    def test_analyse_returns_registered_evidence(self):
        self.build_full_passing_chain()
        run_result = orchestrator.run(allow_dirty=True)
        analysed = orchestrator.analyse()
        self.assertEqual(analysed["run_id"], run_result["run_id"])
        self.assertTrue(analysed["analysed_from_existing_evidence"])

    def test_registry_and_state_updated(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        registry = [e for e in store.load_registry()
                   if e.get("identity", {}).get("experiment_id") == "R2-04"]
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry[0]["identity"]["outcome"], "PASS")
        states = store.load_state()
        self.assertEqual(states["R2-04"]["state"], "COMPLETED")

    def test_two_independent_syntheses_produce_identical_canonical_digest(self):
        """Item: determinism -- two independent analyses over identical
        upstream artifacts must generate an identical canonical digest."""
        r203 = self.build_full_passing_chain()
        budget_results = orchestrator._load_r2_03_budget_results(Path(r203["run_dir"]))
        a = orchestrator._synthesize(budget_results, list(self.SWEEP), self.REPRO_BUDGET)
        b = orchestrator._synthesize(budget_results, list(self.SWEEP), self.REPRO_BUDGET)
        self.assertEqual(orchestrator._canonical_digest(a), orchestrator._canonical_digest(b))

    def test_nondeterminism_forces_fail(self):
        """Item: a forced digest mismatch between the two synthesis calls
        must FAIL, never be silently tolerated."""
        self.build_full_passing_chain()
        real_synthesize = orchestrator._synthesize
        calls = {"n": 0}

        def _flaky_synthesize(*args, **kwargs):
            calls["n"] += 1
            result = real_synthesize(*args, **kwargs)
            if calls["n"] == 2:
                result = dict(result)
                result["missing_budgets"] = list(result["missing_budgets"]) + [999999999]
            return result

        with mock.patch.object(orchestrator, "_synthesize", side_effect=_flaky_synthesize):
            result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_FAIL)
        self.assertTrue(any("canonical digest" in r for r in result["nondeterminism"]))

    def test_missing_budget_forces_inconclusive(self):
        """Item: a declared budget missing a required policy row forces
        INCONCLUSIVE, not FAIL."""
        r203 = self.build_full_passing_chain()
        budget_results = orchestrator._load_r2_03_budget_results(Path(r203["run_dir"]))
        del budget_results[min(budget_results)][LABEL_ORACLE]
        outcome, reasons = orchestrator.classify_outcome(
            mock.Mock(), budget_results, [], [],
            orchestrator._synthesize(budget_results, list(self.SWEEP), self.REPRO_BUDGET))
        self.assertEqual(outcome, orchestrator.OUTCOME_INCONCLUSIVE)

    def test_malformed_upstream_row_forces_fail(self):
        """Item: hits + misses != total_expert_requests in an upstream
        row is a FAIL (malformed upstream evidence), fails closed."""
        r203 = self.build_full_passing_chain()
        budget_results = orchestrator._load_r2_03_budget_results(Path(r203["run_dir"]))
        some_budget = next(iter(budget_results))
        budget_results[some_budget][LABEL_GLOBAL_LRU]["hits"] += 1  # break the invariant
        with self.assertRaises(orchestrator.SynthesisError):
            orchestrator._synthesize(budget_results, list(self.SWEEP), self.REPRO_BUDGET)

    def test_no_model_or_runtime_invocation(self):
        """Item 9/safe-execution: no model/runtime invocation anywhere in
        the R2-04 package."""
        import argine.research2.r2_04 as pkg
        pkg_dir = Path(pkg.__file__).parent
        banned = ("llama-server", "subprocess.Popen", "subprocess.run", "llama.cpp")
        for py_file in pkg_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(token, text, f"{py_file.name} references {token!r}")

    def test_no_fraction_of_oracle_headroom_recovered_is_computed(self):
        """Item: the II-C future metric is documented, never computed,
        anywhere in R2-04's own package."""
        import argine.research2.r2_04 as pkg
        pkg_dir = Path(pkg.__file__).parent
        for py_file in pkg_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if "fraction_of_oracle_headroom_recovered" in text:
                # only allowed to appear as a documented string/formula,
                # never assigned a numeric result from real data
                self.assertNotIn("predictor_miss_bytes =", text)


# ==========================================================================
# runner.py wiring
# ==========================================================================

class RunnerWiringTests(unittest.TestCase):
    def test_r2_04_registered(self):
        self.assertIn("R2-04", runner.EXPERIMENT_RUN_IMPLEMENTATIONS)
        self.assertIn("R2-04", runner.EXPERIMENT_ANALYSE_IMPLEMENTATIONS)


if __name__ == "__main__":
    unittest.main()
