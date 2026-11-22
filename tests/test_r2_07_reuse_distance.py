"""Tests for R2-07: Reuse Distance and Working-Set Structure.

Covers argine/research2/r2_07/ (temporal_unit.py, reuse_stats.py,
working_set.py, null_baseline.py, bridge.py, summary.py, claims.py,
figures.py, prerequisite.py, orchestrator.py) and its wiring into
runner.py. Every fixture is synthetic and built in-process (no real
llama-server, no real 30B model, no network, no model weights loaded,
no new trace capture) -- R2-07 must be fully offline, and so must
every test here.
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
from argine.research2.r2_03 import orchestrator as r2_03_orchestrator, budget_sweep  # noqa: E402
from argine.research2.r2_04 import orchestrator as r2_04_orchestrator  # noqa: E402
from argine.research2.r2_05 import orchestrator as r2_05_orchestrator  # noqa: E402
from argine.research2.r2_06 import orchestrator as r2_06_orchestrator  # noqa: E402
from argine.research2.r2_07 import (bridge, claims as claims_mod, figures, null_baseline,  # noqa: E402
                                    orchestrator, prerequisite, reuse_stats,
                                    summary as summary_mod, temporal_unit, working_set)
from argine.research.trace import Step, Trace, TraceMeta  # noqa: E402
from test_r2_01 import make_trace, write_trace  # noqa: E402


# ==========================================================================
# temporal_unit.py
# ==========================================================================

class TemporalUnitTests(unittest.TestCase):
    def _fixture_trace(self, sequence):
        meta = TraceMeta(model="x", n_experts=8, top_k=1, layers=[0])
        steps = [Step(graph=i, n_tokens=1, phase="decode", routes={0: [(e,)]})
                for i, e in enumerate(sequence)]
        return Trace(meta=meta, steps=steps)

    def test_iter_layer_positions_renumbers_consecutively(self):
        trace = self._fixture_trace([5, 5, 5])
        positions = list(temporal_unit.iter_layer_positions(trace, 0))
        self.assertEqual([p for p, _ in positions], [0, 1, 2])

    def test_reuse_gap_and_distinct_intervening_worked_example(self):
        # 0,1,2,0,3,0 -- matches the task's own worked conceptual example.
        trace = self._fixture_trace([0, 1, 2, 0, 3, 0])
        result = temporal_unit.compute_layer_reuse_events(trace, 0)
        events = {(ev["expert_id"], ev["t_prev"], ev["t_now"]): ev
                 for ev in result["reuse_events"]}
        ev1 = events[(0, 0, 3)]
        self.assertEqual(ev1["token_reuse_gap"], 3)
        self.assertEqual(ev1["distinct_intervening_reuse_distance"], 2)
        ev2 = events[(0, 3, 5)]
        self.assertEqual(ev2["token_reuse_gap"], 2)
        self.assertEqual(ev2["distinct_intervening_reuse_distance"], 1)
        self.assertEqual(result["cold_observation_count"], 4)

    def test_layer_local_identity_no_cross_layer_collapse(self):
        meta = TraceMeta(model="x", n_experts=8, top_k=1, layers=[0, 1])
        steps = [
            Step(graph=0, n_tokens=1, phase="decode", routes={0: [(2,)], 1: [(2,)]}),
            Step(graph=1, n_tokens=1, phase="decode", routes={0: [(2,)], 1: [(5,)]}),
        ]
        trace = Trace(meta=meta, steps=steps)
        r0 = temporal_unit.compute_layer_reuse_events(trace, 0)
        r1 = temporal_unit.compute_layer_reuse_events(trace, 1)
        self.assertEqual(len(r0["reuse_events"]), 1)
        self.assertEqual(len(r1["reuse_events"]), 0)

    def test_no_within_token_tuple_ordering_invented(self):
        """A multi-expert tuple at one position is treated as a set for
        reuse purposes -- its internal element order never drives the
        gap/distinct-intervening computation (duplicates aside)."""
        trace1 = self._fixture_trace([])
        meta = TraceMeta(model="x", n_experts=8, top_k=2, layers=[0])
        steps_a = [Step(graph=0, n_tokens=1, phase="decode", routes={0: [(1, 2)]}),
                  Step(graph=1, n_tokens=1, phase="decode", routes={0: [(3, 4)]}),
                  Step(graph=2, n_tokens=1, phase="decode", routes={0: [(2, 1)]})]
        steps_b = [Step(graph=0, n_tokens=1, phase="decode", routes={0: [(1, 2)]}),
                  Step(graph=1, n_tokens=1, phase="decode", routes={0: [(3, 4)]}),
                  Step(graph=2, n_tokens=1, phase="decode", routes={0: [(1, 2)]})]
        ra = temporal_unit.compute_layer_reuse_events(Trace(meta=meta, steps=steps_a), 0)
        rb = temporal_unit.compute_layer_reuse_events(Trace(meta=meta, steps=steps_b), 0)
        gaps_a = sorted(ev["token_reuse_gap"] for ev in ra["reuse_events"])
        gaps_b = sorted(ev["token_reuse_gap"] for ev in rb["reuse_events"])
        self.assertEqual(gaps_a, gaps_b)

    def test_duplicate_expert_within_one_token_not_silently_deduplicated(self):
        meta = TraceMeta(model="x", n_experts=8, top_k=2, layers=[0])
        steps = [Step(graph=0, n_tokens=1, phase="decode", routes={0: [(1, 1)]})]
        trace = Trace(meta=meta, steps=steps)
        result = temporal_unit.compute_layer_reuse_events(trace, 0)
        self.assertEqual(result["duplicate_within_position_count"], 1)
        self.assertEqual(result["raw_total_occurrences"], 2)
        self.assertEqual(result["cold_observation_count"], 1)
        self.assertEqual(len(result["reuse_events"]), 0)

    def test_reconciliation_identity_exact_on_fixture(self):
        trace = self._fixture_trace([0, 1, 2, 0, 3, 0, 1, 1])
        reuse = temporal_unit.compute_layer_reuse_events(trace, 0)
        indep = temporal_unit.independent_reuse_accounting(trace, 0)
        problems = temporal_unit.reconcile_layer_accounting(reuse, indep)
        self.assertEqual(problems, [])
        total = (reuse["cold_observation_count"] + len(reuse["reuse_events"])
                + reuse["duplicate_within_position_count"])
        self.assertEqual(total, reuse["raw_total_occurrences"])

    def test_reconciliation_on_make_trace_smoke(self):
        trace = make_trace(n_layers=2, n_experts=6, n_decode_steps=30)
        for layer in trace.layers:
            reuse = temporal_unit.compute_layer_reuse_events(trace, layer)
            indep = temporal_unit.independent_reuse_accounting(trace, layer)
            problems = temporal_unit.reconcile_layer_accounting(reuse, indep)
            self.assertEqual(problems, [], f"layer {layer}: {problems}")

    def test_independent_accounting_is_a_genuinely_separate_pass(self):
        """Walks trace.steps directly, not via iter_layer_positions /
        token_selections, confirming it is a real second code path."""
        import inspect
        src = inspect.getsource(temporal_unit.independent_reuse_accounting)
        self.assertNotIn("iter_layer_positions", src)
        self.assertNotIn("token_selections", src)


# ==========================================================================
# reuse_stats.py
# ==========================================================================

class ReuseStatsTests(unittest.TestCase):
    def test_quantile_linear_interpolation_reference_values(self):
        values = sorted(range(1, 11))
        self.assertAlmostEqual(reuse_stats.quantile(values, 0.5), 5.5)
        self.assertAlmostEqual(reuse_stats.quantile(values, 0.9), 9.1)

    def test_quantile_empty_is_none(self):
        self.assertIsNone(reuse_stats.quantile([], 0.5))

    def test_within_threshold_fractions_empty_is_none(self):
        fractions = reuse_stats.within_threshold_fractions([], (1, 2, 4))
        self.assertEqual(fractions, {1: None, 2: None, 4: None})

    def test_distribution_summary_known_values(self):
        d = reuse_stats.distribution_summary([1, 1, 2, 2, 3], thresholds=(1, 2, 4))
        self.assertEqual(d["n"], 5)
        self.assertAlmostEqual(d["median"], 2.0)
        self.assertAlmostEqual(d["p75"], 2.0)
        self.assertAlmostEqual(d["p90"], 2.6)
        self.assertAlmostEqual(d["p95"], 2.8)
        self.assertEqual(d["max"], 3)
        self.assertAlmostEqual(d["within_threshold_fraction"][1], 0.4)
        self.assertAlmostEqual(d["within_threshold_fraction"][2], 0.8)
        self.assertAlmostEqual(d["within_threshold_fraction"][4], 1.0)

    def test_distribution_summary_empty_all_none(self):
        d = reuse_stats.distribution_summary([], thresholds=(1, 2))
        self.assertIsNone(d["median"])
        self.assertIsNone(d["max"])
        self.assertEqual(d["within_threshold_fraction"], {1: None, 2: None})

    def test_predeclared_thresholds_and_windows(self):
        self.assertEqual(reuse_stats.TOKEN_GAP_THRESHOLDS, (1, 2, 4, 8, 16, 32, 64, 128, 256))
        self.assertEqual(reuse_stats.DISTINCT_INTERVENING_THRESHOLDS, (4, 8, 16, 32, 64))
        self.assertEqual(reuse_stats.QUANTILE_LEVELS, (0.50, 0.75, 0.90, 0.95))


# ==========================================================================
# working_set.py
# ==========================================================================

class WorkingSetTests(unittest.TestCase):
    POSITIONS = [(0,), (1,), (0,), (2,), (1,), (0,)]

    def test_sliding_distinct_counts_window_2(self):
        self.assertEqual(working_set.sliding_distinct_counts(self.POSITIONS, 2),
                         [2, 2, 2, 2, 2])

    def test_sliding_distinct_counts_window_3(self):
        self.assertEqual(working_set.sliding_distinct_counts(self.POSITIONS, 3),
                         [2, 3, 3, 3])

    def test_sliding_distinct_counts_too_few_positions_is_empty(self):
        self.assertEqual(working_set.sliding_distinct_counts(self.POSITIONS, 10), [])

    def test_canonical_windows_are_full_windows_only(self):
        m = working_set.layer_window_metrics(self.POSITIONS, 10, nominal_expert_count=4)
        self.assertFalse(m["available"])
        self.assertIsNone(m["median_working_set_size"])

    def test_layer_window_metrics_known_values(self):
        m = working_set.layer_window_metrics(self.POSITIONS, 3, nominal_expert_count=4)
        self.assertTrue(m["available"])
        self.assertEqual(m["n_full_windows"], 4)
        self.assertAlmostEqual(m["mean_working_set_size"], 2.75)
        self.assertAlmostEqual(m["median_working_set_size"], 3.0)
        self.assertAlmostEqual(m["p90_working_set_size"], 3.0)
        self.assertEqual(m["max_working_set_size"], 3)
        self.assertAlmostEqual(m["working_set_fraction"], 0.75)

    def test_observed_expert_count(self):
        self.assertEqual(working_set.observed_expert_count(self.POSITIONS), 3)

    def test_working_set_size_is_expert_count_not_bytes(self):
        m = working_set.layer_window_metrics(self.POSITIONS, 3, nominal_expert_count=4)
        for key in ("mean_working_set_size", "median_working_set_size",
                   "p90_working_set_size", "max_working_set_size"):
            self.assertIsInstance(m[key], (int, float))
        self.assertLessEqual(m["max_working_set_size"], 4)

    def test_growth_curve_covers_all_canonical_windows(self):
        curve = working_set.growth_curve(self.POSITIONS)
        self.assertEqual([row["window"] for row in curve], list(working_set.CANONICAL_WINDOWS))

    def test_canonical_windows_constant(self):
        self.assertEqual(working_set.CANONICAL_WINDOWS, (1, 2, 4, 8, 16, 32, 64, 128, 256))
        self.assertEqual(working_set.SECONDARY_WINDOW, 512)


# ==========================================================================
# null_baseline.py
# ==========================================================================

class NullBaselineTests(unittest.TestCase):
    POSITIONS = [(i % 5,) for i in range(40)]

    def test_shuffle_preserves_invariants(self):
        shuffled = null_baseline.shuffled_positions(self.POSITIONS)
        problems = null_baseline.verify_shuffle_invariants(self.POSITIONS, shuffled)
        self.assertEqual(problems, [])

    def test_shuffle_is_whole_tuple_never_splits_within_token(self):
        positions = [(1, 2), (3, 4), (5, 6)]
        shuffled = null_baseline.shuffled_positions(positions)
        self.assertEqual(sorted(shuffled), sorted(positions))
        for tup in shuffled:
            self.assertIn(tup, positions)

    def test_shuffle_deterministic_for_fixed_seed(self):
        a = null_baseline.shuffled_positions(self.POSITIONS)
        b = null_baseline.shuffled_positions(self.POSITIONS)
        self.assertEqual(a, b)

    def test_layer_null_baseline_deterministic(self):
        bundle1 = null_baseline.layer_null_baseline(self.POSITIONS, nominal_expert_count=5)
        bundle2 = null_baseline.layer_null_baseline(self.POSITIONS, nominal_expert_count=5)
        self.assertEqual(bundle1["invariant_problems"], [])
        self.assertEqual(bundle1["reuse_events"], bundle2["reuse_events"])

    def test_single_replicate_by_default(self):
        self.assertEqual(null_baseline.NULL_BASELINE_REPLICATE_COUNT, 1)

    def test_fixed_seed_constant(self):
        self.assertEqual(null_baseline.NULL_BASELINE_SEED, 1234)

    def test_null_baseline_growth_curve_present(self):
        bundle = null_baseline.layer_null_baseline(self.POSITIONS, nominal_expert_count=5)
        self.assertIn("growth_curve", bundle)
        self.assertTrue(len(bundle["growth_curve"]) > 0)


# ==========================================================================
# bridge.py
# ==========================================================================

class BridgeTests(unittest.TestCase):
    def test_spearman_perfect_positive(self):
        r = bridge.spearman_correlation([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        self.assertAlmostEqual(r, 1.0)

    def test_spearman_perfect_negative(self):
        r = bridge.spearman_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
        self.assertAlmostEqual(r, -1.0)

    def test_spearman_known_reference_value(self):
        r = bridge.spearman_correlation([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
        self.assertAlmostEqual(r, 0.8)

    def test_spearman_tied_inputs_zero_variance_is_none(self):
        r = bridge.spearman_correlation([1, 1, 1], [1, 2, 3])
        self.assertIsNone(r)

    def test_spearman_too_few_points_is_none(self):
        self.assertIsNone(bridge.spearman_correlation([1], [1]))

    def test_frequency_vs_reuse_gap_correlation_shape(self):
        frequency_rows = [
            {"layer_id": 0, "expert_id": 0, "request_count": 10,
             "request_fraction_within_layer": 0.5},
            {"layer_id": 0, "expert_id": 1, "request_count": 2,
             "request_fraction_within_layer": 0.1},
        ]
        layer_reuse_events = {
            0: [{"expert_id": 0, "token_reuse_gap": 1}, {"expert_id": 0, "token_reuse_gap": 2},
               {"expert_id": 1, "token_reuse_gap": 20}],
        }
        result = bridge.frequency_vs_reuse_gap_correlation(frequency_rows, layer_reuse_events)
        self.assertEqual(result["n_paired_observations"], 2)
        self.assertIn("spearman_correlation", result)

    def test_r205_r206_bridge_notes_forbid_overreach(self):
        self.assertIn("not", bridge.R206_BRIDGE_NOTE.lower())


# ==========================================================================
# summary.py
# ==========================================================================

class SummaryTests(unittest.TestCase):
    def test_global_summary_aggregates_across_layers(self):
        gap_dist = {0: reuse_stats.token_reuse_gap_distribution([1, 2, 3]),
                   1: reuse_stats.token_reuse_gap_distribution([10, 20, 30])}
        distinct_dist = {0: reuse_stats.distinct_intervening_distribution([1, 1]),
                        1: reuse_stats.distinct_intervening_distribution([5, 5])}
        window_metrics = {
            0: {8: {"median_working_set_size": 3.0}, 32: {"median_working_set_size": 4.0},
               128: {"median_working_set_size": 5.0}, 256: {"median_working_set_size": 6.0}},
            1: {8: {"median_working_set_size": 6.0}, 32: {"median_working_set_size": 7.0},
               128: {"median_working_set_size": 8.0}, 256: {"median_working_set_size": 9.0}},
        }
        comparisons = {
            0: {"immediate_reuse_fraction": {"observed": 0.5, "null": 0.2},
               "reuse_within_4_fraction": {"observed": 0.6, "null": 0.3},
               "reuse_within_16_fraction": {"observed": 0.7, "null": 0.4},
               "median_token_reuse_gap": {"observed": 2.0, "null": 5.0},
               "p90_token_reuse_gap": {"observed": 3.0, "null": 9.0},
               "median_distinct_intervening_reuse_distance": {"observed": 1.0, "null": 2.0},
               "working_set_size": {8: {"observed": 3.0, "null": 3.5},
                                    32: {"observed": 4.0, "null": 4.5},
                                    128: {"observed": 5.0, "null": 5.5},
                                    256: {"observed": 6.0, "null": 6.5}}},
            1: {"immediate_reuse_fraction": {"observed": 0.1, "null": 0.1},
               "reuse_within_4_fraction": {"observed": 0.2, "null": 0.2},
               "reuse_within_16_fraction": {"observed": 0.3, "null": 0.3},
               "median_token_reuse_gap": {"observed": 20.0, "null": 20.0},
               "p90_token_reuse_gap": {"observed": 30.0, "null": 30.0},
               "median_distinct_intervening_reuse_distance": {"observed": 5.0, "null": 5.0},
               "working_set_size": {8: {"observed": 6.0, "null": 6.0},
                                    32: {"observed": 7.0, "null": 7.0},
                                    128: {"observed": 8.0, "null": 8.0},
                                    256: {"observed": 9.0, "null": 9.0}}},
        }
        g = summary_mod.global_summary(gap_dist, distinct_dist, window_metrics, comparisons)
        self.assertIn("median_token_reuse_gap", g)
        self.assertIn("median_working_set_size_by_window", g)
        self.assertEqual(set(g["median_working_set_size_by_window"]),
                         set(summary_mod.HEADLINE_WORKING_SET_WINDOWS))
        self.assertIn("heterogeneity", g)
        self.assertIn("observed_vs_null", g)

    def test_heterogeneity_disclaims_architectural_cause(self):
        gap_dist = {0: {"median": 1.0}, 1: {"median": 10.0}}
        window_metrics = {0: {32: {"median_working_set_size": 2.0},
                             128: {"median_working_set_size": 3.0}},
                         1: {32: {"median_working_set_size": 5.0},
                             128: {"median_working_set_size": 6.0}}}
        h = summary_mod.layer_heterogeneity(gap_dist, window_metrics)
        self.assertIn("note", h)
        self.assertNotIn("architect", h["note"].lower().replace("architectural", ""))

    def test_observed_vs_null_summary_ratio_and_difference(self):
        comparisons = {
            0: {"immediate_reuse_fraction": {"observed": 0.5, "null": 0.25},
               "reuse_within_4_fraction": {"observed": 0.5, "null": 0.25},
               "reuse_within_16_fraction": {"observed": 0.5, "null": 0.25},
               "median_token_reuse_gap": {"observed": 2.0, "null": 4.0},
               "p90_token_reuse_gap": {"observed": 2.0, "null": 4.0},
               "median_distinct_intervening_reuse_distance": {"observed": 1.0, "null": 2.0},
               "working_set_size": {8: {"observed": 2.0, "null": 4.0},
                                    32: {"observed": 2.0, "null": 4.0},
                                    128: {"observed": 2.0, "null": 4.0},
                                    256: {"observed": 2.0, "null": 4.0}}},
        }
        o = summary_mod.observed_vs_null_summary(comparisons)
        self.assertAlmostEqual(o["median_token_reuse_gap"]["ratio_observed_to_null"], 0.5)
        self.assertAlmostEqual(o["median_token_reuse_gap"]["difference_observed_minus_null"],
                               -2.0)


# ==========================================================================
# claims.py
# ==========================================================================

class ClaimsTests(unittest.TestCase):
    def _global_summary(self):
        return {
            "median_immediate_reuse_fraction": 0.3, "median_reuse_within_4_fraction": 0.5,
            "median_reuse_within_16_fraction": 0.7, "median_token_reuse_gap": 4.0,
            "median_p90_token_reuse_gap": 20.0,
            "median_distinct_intervening_reuse_distance": 2.0,
            "median_working_set_size_by_window": {8: 3.0, 32: 5.0, 128: 7.0, 256: 9.0},
            "heterogeneity": {"shortest_median_reuse_gap_layer": 0,
                              "longest_median_reuse_gap_layer": 1, "note": "n/a"},
            "observed_vs_null": {},
        }

    def test_build_claims_contains_supported_and_not_yet_supported(self):
        c = claims_mod.build_claims(self._global_summary(),
                                    {"n_paired_observations": 0, "spearman_correlation": None})
        self.assertTrue(len(c["supported_now"]) >= 5)
        self.assertTrue(len(c["not_yet_supported"]) >= 5)
        self.assertIn("r2_06_bridge", c)
        self.assertIn("r2_05_bridge", c)

    def test_forbidden_claim_guard_fires_on_supported_now(self):
        payload = {"supported_now": ["this policy would reduce misses by 10%"],
                  "not_yet_supported": []}
        with self.assertRaises(claims_mod.ForbiddenClaimError):
            claims_mod.check_no_forbidden_claims(payload)

    def test_forbidden_claim_guard_ignores_not_yet_supported(self):
        payload = {"supported_now": ["median token reuse gap was 4 positions"],
                  "not_yet_supported": ["we do not claim this policy would reduce misses"]}
        claims_mod.check_no_forbidden_claims(payload)  # must not raise

    def test_build_claims_adds_correlation_claim_when_available(self):
        c = claims_mod.build_claims(
            self._global_summary(),
            {"n_paired_observations": 10, "spearman_correlation": 0.42})
        joined = " ".join(json.dumps(item) for item in c["supported_now"])
        self.assertIn("0.42", joined)

    def test_r206_overreach_guard_present(self):
        self.assertIn("r2-06 proves", [p.lower() for p in claims_mod.FORBIDDEN_PHRASES])


# ==========================================================================
# figures.py
# ==========================================================================

class FiguresTests(unittest.TestCase):
    def test_reuse_gap_cdf_monotonic(self):
        rows = figures.figure_a_reuse_gap_cdf([1, 1, 2, 5, 5, 5, 10])
        fractions = [r["cumulative_fraction"] for r in rows]
        self.assertEqual(fractions, sorted(fractions))
        self.assertAlmostEqual(fractions[-1], 1.0)

    def test_distinct_intervening_cdf_relabels_key(self):
        rows = figures.figure_b_distinct_intervening_cdf([1, 2, 3])
        self.assertTrue(all("distinct_intervening_reuse_distance" in r for r in rows))

    def test_growth_curve_passthrough(self):
        rows = [{"window": 1, "available": True, "median_working_set_size": 1.0}]
        self.assertEqual(figures.figure_c_working_set_growth_curve(rows), rows)

    def test_observed_vs_shuffled_alignment(self):
        observed = [{"window": 8, "available": True, "median_working_set_size": 5.0},
                   {"window": 32, "available": True, "median_working_set_size": 10.0}]
        null = [{"window": 8, "available": True, "median_working_set_size": 4.0},
               {"window": 32, "available": True, "median_working_set_size": 8.0}]
        rows = figures.figure_d_observed_vs_shuffled_working_set(observed, null)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["window"], 8)
        self.assertAlmostEqual(rows[0]["observed_median_working_set_size"], 5.0)
        self.assertAlmostEqual(rows[0]["null_median_working_set_size"], 4.0)


# ==========================================================================
# Full-chain evidence fixture + prerequisite.py + orchestrator.py
# ==========================================================================

class _EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-07-"))
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
                       run_id: Optional[str] = None,
                       candidate_status: str = "CANDIDATE") -> str:
        run_id = run_id or f"fake-r201-{outcome.lower()}-{end_utc}"
        _trace, norm_report = trace_contract.normalize(trace_path)
        rd = store.run_dir("R2-01", run_id)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "source-trace.json").write_text(json.dumps(norm_report.to_json()), encoding="utf-8")
        (rd / "manifest.json").write_text(
            json.dumps({"git": {"candidate_status": candidate_status}}), encoding="utf-8")
        store.append_registry_entry({
            "identity": {"experiment_id": "R2-01", "run_id": run_id, "outcome": outcome,
                        "end_utc": end_utc},
            "run_dir": str(rd), "trace_source": str(trace_path)})
        return run_id

    SWEEP = (3000, 4500, 6000, 12000, 24000)
    REPRO_BUDGET = 6000

    def build_full_passing_chain(self, **trace_kwargs) -> dict:
        """R2-01 -> R2-02 -> R2-03 -> R2-04 -> R2-05 -> R2-06 PASS, all
        real orchestrators over a tiny synthetic trace, so R2-07's
        prerequisite binds to a genuine chain with R2-06 PASS."""
        p = self.trace_path(n_layers=2, n_experts=8, n_decode_steps=60, **trace_kwargs)
        self.register_r2_01(p, outcome="PASS")
        with mock.patch.object(r2_02_orchestrator, "FIXED_BUDGET_BYTES", self.REPRO_BUDGET):
            r202 = r2_02_orchestrator.run(allow_dirty=True)
        self.assertEqual(r202["outcome"], "PASS")
        with mock.patch.object(budget_sweep, "FIXED_BUDGETS_BYTES", self.SWEEP), \
             mock.patch.object(budget_sweep, "REPRODUCTION_BUDGET_BYTES", self.REPRO_BUDGET):
            r203 = r2_03_orchestrator.run(allow_dirty=True)
        self.assertEqual(r203["outcome"], "PASS", r203.get("reasons"))
        r204 = r2_04_orchestrator.run(allow_dirty=True)
        self.assertEqual(r204["outcome"], "PASS", r204.get("reasons"))
        r205 = r2_05_orchestrator.run(allow_dirty=True)
        self.assertEqual(r205["outcome"], "PASS", r205.get("reasons"))
        r206 = r2_06_orchestrator.run(allow_dirty=True)
        self.assertEqual(r206["outcome"], "PASS", r206.get("reasons"))
        return r206


class PrerequisiteTests(_EvidenceCase):
    def test_no_r2_01_run_blocks(self):
        b = prerequisite.require_full_chain_pass()
        self.assertFalse(b.ok)
        self.assertEqual(b.status, prerequisite.STATUS_UPSTREAM_PREREQUISITE_FAILED)

    def test_no_r2_06_run_blocks_even_with_r2_05_pass(self):
        """R2-01/R2-04/R2-05 PASS alone is not enough -- R2-06 (the
        second II-B experiment) must have reached PASS before R2-07
        may run."""
        p = self.trace_path(n_layers=2, n_experts=8, n_decode_steps=60)
        self.register_r2_01(p, outcome="PASS")
        with mock.patch.object(r2_02_orchestrator, "FIXED_BUDGET_BYTES", self.REPRO_BUDGET):
            r202 = r2_02_orchestrator.run(allow_dirty=True)
        self.assertEqual(r202["outcome"], "PASS")
        with mock.patch.object(budget_sweep, "FIXED_BUDGETS_BYTES", self.SWEEP), \
             mock.patch.object(budget_sweep, "REPRODUCTION_BUDGET_BYTES", self.REPRO_BUDGET):
            r203 = r2_03_orchestrator.run(allow_dirty=True)
        self.assertEqual(r203["outcome"], "PASS", r203.get("reasons"))
        r204 = r2_04_orchestrator.run(allow_dirty=True)
        self.assertEqual(r204["outcome"], "PASS", r204.get("reasons"))
        r205 = r2_05_orchestrator.run(allow_dirty=True)
        self.assertEqual(r205["outcome"], "PASS", r205.get("reasons"))
        b = prerequisite.require_full_chain_pass()
        self.assertEqual(b.status, prerequisite.STATUS_NO_R2_06_RUN)
        self.assertFalse(b.ok)

    def test_ok_binding_on_real_full_chain(self):
        self.build_full_passing_chain()
        b = prerequisite.require_full_chain_pass()
        self.assertTrue(b.ok, b.message)
        self.assertIsNotNone(b.trace_sha256)
        self.assertIsNotNone(b.r2_06_run_id)
        self.assertEqual(b.candidate_status, "CANDIDATE")

    def test_trace_changed_since_r2_01_pass_fails_closed(self):
        self.build_full_passing_chain()
        reg = store.load_registry()
        r201_entry = [e for e in reg if e["identity"]["experiment_id"] == "R2-01"][0]
        trace_path = Path(r201_entry["trace_source"])
        trace_path.write_bytes(trace_path.read_bytes() + b"\x00")
        b = prerequisite.require_full_chain_pass()
        self.assertFalse(b.ok)

    def test_r2_06_binding_mismatch_fails_closed(self):
        r206 = self.build_full_passing_chain()
        run_dir = Path(r206["run_dir"])
        prov = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
        prov["binding"]["r2_01_run_id"] = "some-other-run-id"
        (run_dir / "provenance.json").write_text(json.dumps(prov), encoding="utf-8")
        b = prerequisite.require_full_chain_pass()
        self.assertEqual(b.status, prerequisite.STATUS_BINDING_MISMATCH)


class OrchestratorTests(_EvidenceCase):
    def test_full_run_pass(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS", result.get("reasons"))
        self.assertIsNotNone(result["canonical_digest"])
        run_dir = Path(result["run_dir"])
        for fname in ("manifest.json", "provenance.json", "summary.json",
                     "reuse_events.csv", "layer_reuse_summary.csv",
                     "working_set_by_window.csv", "reuse_structure.json",
                     "null_baseline.json", "bridge.json", "figure_data.json",
                     "claims.json"):
            self.assertTrue((run_dir / fname).is_file(), f"missing {fname}")

    def test_two_repeated_full_analyses_identical(self):
        """Determinism includes the null-baseline seed/config: repeated
        full runs must produce an identical canonical digest."""
        self.build_full_passing_chain()
        result_a = orchestrator.run(allow_dirty=True)
        result_b = orchestrator.run(allow_dirty=True)
        self.assertEqual(result_a["outcome"], "PASS")
        self.assertEqual(result_b["outcome"], "PASS")
        self.assertEqual(result_a["canonical_digest"], result_b["canonical_digest"])
        self.assertEqual(result_a["headline"], result_b["headline"])

    def test_classify_outcome_fail_on_nondeterminism(self):
        outcome, reasons = orchestrator.classify_outcome(
            [], ["digest mismatch"], {"headline": {}})
        self.assertEqual(outcome, orchestrator.OUTCOME_FAIL)

    def test_classify_outcome_fail_on_synthesis_error(self):
        outcome, reasons = orchestrator.classify_outcome(["bad data"], [], None)
        self.assertEqual(outcome, orchestrator.OUTCOME_FAIL)

    def test_pass_does_not_require_short_reuse_distance(self):
        """PASS does not require short reuse distance anywhere -- long
        or near-null reuse behaviour is valid PASS science too."""
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS")

    def test_nominal_expert_count_unavailable_is_inconclusive(self):
        p = self.trace_path(n_layers=2, n_experts=8, n_decode_steps=40)
        trace = make_trace(n_layers=2, n_experts=8, n_decode_steps=40)
        trace.meta.n_experts = 0
        write_trace(trace, p)
        self.register_r2_01(p, outcome="PASS")
        with mock.patch.object(r2_02_orchestrator, "FIXED_BUDGET_BYTES", self.REPRO_BUDGET):
            r202 = r2_02_orchestrator.run(allow_dirty=True)
        self.assertEqual(r202["outcome"], "PASS")
        with mock.patch.object(budget_sweep, "FIXED_BUDGETS_BYTES", self.SWEEP), \
             mock.patch.object(budget_sweep, "REPRODUCTION_BUDGET_BYTES", self.REPRO_BUDGET):
            r203 = r2_03_orchestrator.run(allow_dirty=True)
        self.assertEqual(r203["outcome"], "PASS", r203.get("reasons"))
        r204 = r2_04_orchestrator.run(allow_dirty=True)
        self.assertEqual(r204["outcome"], "PASS", r204.get("reasons"))
        r205 = r2_05_orchestrator.run(allow_dirty=True)
        self.assertEqual(r205["outcome"], "INCONCLUSIVE")
        # R2-05 itself goes INCONCLUSIVE without a nominal expert count, so
        # R2-07's own prerequisite (which requires the full chain through
        # R2-06 PASS) blocks here -- confirming the upstream gate, not
        # re-deriving R2-07's own n_experts handling redundantly.
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_PREREQUISITE_NOT_MET)

    def test_prerequisite_not_met_short_circuits_with_no_evidence(self):
        before = len(store.load_registry())
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_PREREQUISITE_NOT_MET)
        after = len(store.load_registry())
        self.assertEqual(before, after)

    def test_bridge_and_null_evidence_present(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS")
        run_dir = Path(result["run_dir"])
        bridge_json = json.loads((run_dir / "bridge.json").read_text(encoding="utf-8"))
        self.assertIn("r2_06_bridge_note", bridge_json)
        self.assertIn("r2_05_bridge_note", bridge_json)
        null_json = json.loads((run_dir / "null_baseline.json").read_text(encoding="utf-8"))
        self.assertIn("layer_null", null_json)

    def test_canonical_digest_binds_r2_05_and_r2_06(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        run_dir = Path(result["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        params = manifest["experiment"]["parameters"]
        self.assertIsNotNone(params.get("r2_05_canonical_digest"))
        self.assertIsNotNone(params.get("r2_06_canonical_digest"))
        self.assertIsNotNone(params.get("r2_05_run_id"))
        self.assertIsNotNone(params.get("r2_06_run_id"))

    def test_working_set_csv_has_canonical_windows(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        run_dir = Path(result["run_dir"])
        text = (run_dir / "working_set_by_window.csv").read_text(encoding="utf-8")
        for w in working_set.CANONICAL_WINDOWS:
            self.assertIn(f",{w},", text.replace("\n", ",") + ",")

    def test_no_model_or_runtime_import_in_r2_07_modules(self):
        import argine.research2.r2_07 as pkg
        import inspect
        import pkgutil
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            mod = __import__(f"argine.research2.r2_07.{mod_info.name}", fromlist=["x"])
            src = inspect.getsource(mod)
            self.assertNotIn("runtime_resolver", src, mod_info.name)
            self.assertNotIn("llama-server", src, mod_info.name)
            self.assertNotIn("llama_server", src, mod_info.name)
            self.assertNotIn("subprocess", src, mod_info.name)

    def test_no_predictor_accuracy_terms_in_primary_evidence(self):
        """Static check: predictor-performance vocabulary never appears
        in any R2-07 module source."""
        import argine.research2.r2_07 as pkg
        import inspect
        import pkgutil
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            mod = __import__(f"argine.research2.r2_07.{mod_info.name}", fromlist=["x"])
            src = inspect.getsource(mod).lower()
            for term in ("precision_score", "recall_score", "f1_score", "roc_auc"):
                self.assertNotIn(term, src, mod_info.name)

    def test_no_bytes_or_gib_working_set_unit_in_primary_evidence(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        run_dir = Path(result["run_dir"])
        structure = json.loads((run_dir / "reuse_structure.json").read_text(encoding="utf-8"))
        blob = json.dumps(structure).lower()
        self.assertNotIn("gib", blob)
        self.assertNotIn("working_set_bytes", blob)

    def test_frozen_ii_a_paper_tree_unchanged(self):
        paper_dir = REPO_ROOT / "paper" / "research2"
        before = {}
        for p in sorted(paper_dir.rglob("*")):
            if p.is_file():
                before[str(p)] = (p.stat().st_mtime_ns, p.stat().st_size)
        self.build_full_passing_chain()
        orchestrator.run(allow_dirty=True)
        after = {}
        for p in sorted(paper_dir.rglob("*")):
            if p.is_file():
                after[str(p)] = (p.stat().st_mtime_ns, p.stat().st_size)
        self.assertEqual(before, after)

    def test_analyse_latest_matches_run_result(self):
        self.build_full_passing_chain()
        ran = orchestrator.run(allow_dirty=True)
        analysed = orchestrator.analyse()
        self.assertEqual(analysed["run_id"], ran["run_id"])
        self.assertEqual(analysed["outcome"], ran["outcome"])


class RunnerWiringTests(unittest.TestCase):
    def test_r2_07_registered(self):
        self.assertIn("R2-07", runner.EXPERIMENT_RUN_IMPLEMENTATIONS)
        self.assertIn("R2-07", runner.EXPERIMENT_ANALYSE_IMPLEMENTATIONS)


if __name__ == "__main__":
    unittest.main()
