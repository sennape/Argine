"""Tests for R2-06: Routing Transition Structure.

Covers argine/research2/r2_06/ (transition_unit.py, matrix.py,
info_metrics.py, support.py, concentration.py, null_baseline.py,
bridge.py, summary.py, claims.py, prerequisite.py, orchestrator.py) and
its wiring into runner.py. Every fixture is synthetic and built
in-process (no real llama-server, no real 30B model, no network, no
model weights loaded, no new trace capture) -- R2-06 must be fully
offline, and so must every test here.
"""

from __future__ import annotations

import json
import math
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
from argine.research2.r2_06 import (bridge, claims, concentration,  # noqa: E402
                                    info_metrics, matrix, null_baseline,
                                    orchestrator, prerequisite, summary as summary_mod,
                                    support, transition_unit)
from argine.research.trace import Step, Trace, TraceMeta  # noqa: E402
from test_r2_01 import make_trace, write_trace  # noqa: E402


# ==========================================================================
# transition_unit.py
# ==========================================================================

class TransitionUnitTests(unittest.TestCase):
    def test_layer_local_identity_no_cross_layer_collapse(self):
        """Same (i, j) pair in two different layers counted independently,
        never merged."""
        meta = TraceMeta(model="x", n_experts=4, top_k=1, layers=[0, 1])
        steps = [
            Step(graph=0, n_tokens=1, phase="decode", routes={0: [(2,)], 1: [(3,)]}),
            Step(graph=1, n_tokens=1, phase="decode", routes={0: [(1,)], 1: [(0,)]}),
        ]
        trace = Trace(meta=meta, steps=steps)
        counts = transition_unit.count_transitions_per_layer(trace)
        self.assertEqual(counts[0], {(2, 1): 1})
        self.assertEqual(counts[1], {(3, 0): 1})

    def test_consecutive_pairs_use_iterator_order_not_token_index(self):
        """'Consecutive' means consecutive in token_selections' own
        yielded order for THIS layer, not consecutive token_index; a
        layer that is silent for some tokens still transitions correctly
        across the gap."""
        meta = TraceMeta(model="x", n_experts=4, top_k=1, layers=[0, 1])
        steps = [
            Step(graph=0, n_tokens=1, phase="decode", routes={0: [(1,)]}),
            Step(graph=1, n_tokens=1, phase="decode", routes={1: [(0,)]}),  # layer 0 silent
            Step(graph=2, n_tokens=1, phase="decode", routes={0: [(2,)]}),
        ]
        trace = Trace(meta=meta, steps=steps)
        counts = transition_unit.count_transitions_per_layer(trace)
        # layer 0 only ever sees two entries (1 then 2) despite the gap
        self.assertEqual(counts[0], {(1, 2): 1})

    def test_pairwise_set_to_set_expansion_multi_expert(self):
        """Section 6: k_src * k_tgt pairwise observations per transition
        step, every pair weight 1, never deduplicated."""
        meta = TraceMeta(model="x", n_experts=4, top_k=2, layers=[0])
        steps = [
            Step(graph=0, n_tokens=1, phase="decode", routes={0: [(0, 1)]}),
            Step(graph=1, n_tokens=1, phase="decode", routes={0: [(2, 3)]}),
        ]
        trace = Trace(meta=meta, steps=steps)
        counts = transition_unit.count_transitions_per_layer(trace)
        self.assertEqual(sum(counts[0].values()), 4)  # 2 * 2
        for i in (0, 1):
            for j in (2, 3):
                self.assertEqual(counts[0][(i, j)], 1)

    def test_single_selection_layer_yields_no_transitions(self):
        """A layer with only one routed token has no 'next' -- zero
        transitions, not an error."""
        meta = TraceMeta(model="x", n_experts=4, top_k=1, layers=[0])
        steps = [Step(graph=0, n_tokens=1, phase="decode", routes={0: [(1,)]})]
        trace = Trace(meta=meta, steps=steps)
        counts = transition_unit.count_transitions_per_layer(trace)
        self.assertEqual(counts[0], {})

    def test_reconciliation_matches_independent_computation(self):
        trace = make_trace(n_layers=2, n_experts=6, n_decode_steps=25)
        counts = transition_unit.count_transitions_per_layer(trace)
        indep = transition_unit.independent_count_transitions_per_layer(trace)
        indep_sums = {layer: sum(c.values()) for layer, c in indep.items()}
        problems = transition_unit.reconcile_total_transitions(counts, indep_sums)
        self.assertEqual(problems, [])
        for layer in counts:
            self.assertEqual(counts[layer], indep[layer])

    def test_reconciliation_fails_closed_on_mismatch(self):
        problems = transition_unit.reconcile_total_transitions({0: {(1, 1): 5}}, {0: 999})
        self.assertTrue(problems)

    def test_expert_id_range_validation_fails_closed(self):
        self.assertFalse(transition_unit.expert_id_range_valid({0: {(99, 1): 1}}, n_experts=6))
        self.assertFalse(transition_unit.expert_id_range_valid({0: {(1, 99): 1}}, n_experts=6))
        self.assertTrue(transition_unit.expert_id_range_valid({0: {(1, 5): 1}}, n_experts=6))

    def test_layer_selection_sequence_matches_token_selections(self):
        trace = make_trace(n_layers=1, n_experts=4, n_decode_steps=10)
        layer = trace.layers[0]
        seq = transition_unit.layer_selection_sequence(trace, layer)
        expected = [sel for _t, sel in trace.token_selections(layer)]
        self.assertEqual(seq, expected)

    def test_cross_layer_not_counted_by_primary_unit(self):
        """Section 4: the primary unit never counts a transition BETWEEN
        two different layers; only within one layer's own iterator."""
        trace = make_trace(n_layers=3, n_experts=4, n_decode_steps=5)
        counts = transition_unit.count_transitions_per_layer(trace)
        self.assertEqual(set(counts.keys()), set(trace.layers))


# ==========================================================================
# matrix.py
# ==========================================================================

class MatrixTests(unittest.TestCase):
    def test_conditional_and_lift_basic(self):
        counts = {(0, 0): 3, (0, 1): 1, (1, 0): 2, (1, 1): 2}
        table = matrix.layer_transition_table(0, counts)
        self.assertAlmostEqual(table["conditional"][0][0], 0.75)
        self.assertAlmostEqual(table["conditional"][0][1], 0.25)
        self.assertAlmostEqual(table["target_marginal"][0], 5 / 8)
        self.assertAlmostEqual(table["target_marginal"][1], 3 / 8)
        expected_lift_0_0 = 0.75 / (5 / 8)
        self.assertAlmostEqual(table["lift"][0][0], expected_lift_0_0)

    def test_zero_denominator_fails_closed(self):
        """Fail-closed: a source with zero outgoing mass or a target with
        zero marginal mass never produces a fabricated 0.0."""
        table = matrix.layer_transition_table(0, {})
        self.assertEqual(table["conditional"], {})
        self.assertEqual(table["total_transition_mass"], 0)

    def test_probability_mass_valid_detects_corruption(self):
        counts = {(0, 0): 3, (0, 1): 1}
        table = matrix.layer_transition_table(0, counts)
        self.assertEqual(matrix.probability_mass_valid({0: table}), [])
        # manufacture a corrupted conditional row
        table["conditional"][0][0] = 0.1
        problems = matrix.probability_mass_valid({0: table})
        self.assertTrue(problems)

    def test_transition_rows_deterministic_order(self):
        counts = {(1, 0): 1, (0, 1): 1, (0, 0): 1}
        table = matrix.layer_transition_table(0, counts)
        rows_a = matrix.transition_rows(0, table)
        rows_b = matrix.transition_rows(0, table)
        self.assertEqual(rows_a, rows_b)
        pairs = [(r["source_expert_id"], r["target_expert_id"]) for r in rows_a]
        self.assertEqual(pairs, sorted(pairs))

    def test_transition_rows_counts_match_raw_counts(self):
        counts = {(0, 0): 7, (0, 1): 3}
        table = matrix.layer_transition_table(0, counts)
        rows = matrix.transition_rows(0, table)
        by_pair = {(r["source_expert_id"], r["target_expert_id"]): r["transition_count"]
                  for r in rows}
        self.assertEqual(by_pair[(0, 0)], 7)
        self.assertEqual(by_pair[(0, 1)], 3)


# ==========================================================================
# info_metrics.py
# ==========================================================================

class InfoMetricsTests(unittest.TestCase):
    def test_independent_source_and_target_gives_zero_mi(self):
        """When P(j|i) == P(j) for every i (independence), MI must be
        (approximately) zero."""
        counts = {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 1}
        table = matrix.layer_transition_table(0, counts)
        info = info_metrics.layer_information_metrics(table)
        self.assertAlmostEqual(info["mutual_information"], 0.0, places=9)
        self.assertAlmostEqual(info["normalized_mutual_information"], 0.0, places=9)

    def test_fully_deterministic_transition_gives_max_mi(self):
        """source i always transitions to target i: H(Y|X) = 0, so
        I(X;Y) = H(Y) and NMI = 1.0."""
        counts = {(0, 0): 10, (1, 1): 10}
        table = matrix.layer_transition_table(0, counts)
        info = info_metrics.layer_information_metrics(table)
        self.assertAlmostEqual(info["conditional_entropy"], 0.0, places=9)
        self.assertAlmostEqual(info["normalized_mutual_information"], 1.0, places=9)

    def test_nmi_none_when_marginal_entropy_zero(self):
        """Every transition lands on a single target: H(Y) == 0, so NMI
        is undefined (None), never a fabricated 0.0."""
        counts = {(0, 0): 5, (1, 0): 5}
        table = matrix.layer_transition_table(0, counts)
        info = info_metrics.layer_information_metrics(table)
        self.assertAlmostEqual(info["marginal_entropy"], 0.0, places=9)
        self.assertIsNone(info["normalized_mutual_information"])

    def test_mutual_information_never_negative(self):
        import random
        rng = random.Random(42)
        counts = {(i, j): rng.randint(1, 20) for i in range(5) for j in range(5)}
        table = matrix.layer_transition_table(0, counts)
        info = info_metrics.layer_information_metrics(table)
        self.assertGreaterEqual(info["mutual_information"], 0.0)

    def test_no_sources_returns_none_fields(self):
        table = matrix.layer_transition_table(0, {})
        info = info_metrics.layer_information_metrics(table)
        self.assertIsNone(info["mutual_information"])
        self.assertIsNone(info["normalized_mutual_information"])


# ==========================================================================
# support.py
# ==========================================================================

class SupportTests(unittest.TestCase):
    def test_thresholds_are_predeclared_fixed_constants(self):
        self.assertIsInstance(support.MIN_SOURCE_SUPPORT, int)
        self.assertIsInstance(support.MIN_PAIR_SUPPORT, int)
        self.assertGreater(support.MIN_SOURCE_SUPPORT, 0)
        self.assertGreater(support.MIN_PAIR_SUPPORT, 0)

    def test_supported_sources_filters_by_row_sum(self):
        counts = {(0, 0): 50, (1, 0): 2}
        table = matrix.layer_transition_table(0, counts)
        sources = support.supported_sources(table)
        self.assertIn(0, sources)
        self.assertNotIn(1, sources)

    def test_supported_pairs_filters_by_cell_count(self):
        counts = {(0, 0): 50, (0, 1): 2}
        table = matrix.layer_transition_table(0, counts)
        pairs = support.supported_pairs(table)
        self.assertIn((0, 0), pairs)
        self.assertNotIn((0, 1), pairs)

    def test_strongest_supported_lift_excludes_rare_cells(self):
        """A very rare but very high-lift cell must not be reported as
        the strongest supported lift."""
        counts = {(0, 0): 1, (0, 1): 50, (1, 1): 50, (1, 0): 1}
        table = matrix.layer_transition_table(0, counts)
        result = support.strongest_supported_lift(table)
        self.assertIsNotNone(result)
        i, j, lift_value = result
        self.assertGreaterEqual(table["counts"][(i, j)], support.MIN_PAIR_SUPPORT)

    def test_strongest_supported_lift_none_when_nothing_qualifies(self):
        counts = {(0, 0): 1, (0, 1): 1}
        table = matrix.layer_transition_table(0, counts)
        self.assertIsNone(support.strongest_supported_lift(table))

    def test_raw_rows_never_filtered_by_support(self):
        """Rare cells remain visible in transition_rows regardless of the
        support threshold -- only headline/strong-transition summaries
        apply the filter."""
        counts = {(0, 0): 1}
        table = matrix.layer_transition_table(0, counts)
        rows = matrix.transition_rows(0, table)
        self.assertEqual(len(rows), 1)


# ==========================================================================
# concentration.py
# ==========================================================================

class ConcentrationTests(unittest.TestCase):
    def test_most_likely_next_expert_and_top_k_share(self):
        counts = {(0, 0): 5, (0, 1): 3, (0, 2): 2}
        table = matrix.layer_transition_table(0, counts)
        sc = concentration.source_concentration(table, 0)
        self.assertEqual(sc["most_likely_next_expert"], 0)
        self.assertAlmostEqual(sc["top_1_target_share"], 0.5)
        self.assertAlmostEqual(sc["top_k_target_share"][4], 1.0)

    def test_source_with_no_outgoing_mass_fails_closed(self):
        table = matrix.layer_transition_table(0, {(1, 0): 1})
        sc = concentration.source_concentration(table, 99)
        self.assertIsNone(sc["most_likely_next_expert"])
        self.assertIsNone(sc["outgoing_conditional_entropy"])

    def test_self_transition_metrics_do_not_assume_dominance(self):
        counts = {(0, 1): 10, (1, 0): 10}  # no self-transitions at all
        table = matrix.layer_transition_table(0, counts)
        st = concentration.self_transition_metrics(table)
        self.assertEqual(st["aggregate_self_transition_mass"], 0)
        self.assertEqual(st["aggregate_self_transition_share"], 0.0)

    def test_self_transition_lift_measured_when_present(self):
        counts = {(0, 0): 9, (0, 1): 1}
        table = matrix.layer_transition_table(0, counts)
        st = concentration.self_transition_metrics(table)
        row = [r for r in st["rows"] if r["expert_id"] == 0][0]
        self.assertAlmostEqual(row["self_transition_probability"], 0.9)
        self.assertIsNotNone(row["self_transition_lift"])

    def test_layer_aggregate_concentration_summarizes_rows(self):
        counts = {(0, 0): 5, (0, 1): 5, (1, 1): 10}
        table = matrix.layer_transition_table(0, counts)
        rows = concentration.layer_source_concentration_rows(table)
        agg = concentration.layer_aggregate_concentration(rows)
        self.assertIsNotNone(agg["median_top_1_target_share"])


# ==========================================================================
# null_baseline.py
# ==========================================================================

class NullBaselineTests(unittest.TestCase):
    def test_analytic_independence_gives_zero_mutual_information(self):
        counts = {(0, 0): 3, (0, 1): 7, (1, 0): 3, (1, 1): 7}
        table = matrix.layer_transition_table(0, counts)
        indep_table = null_baseline.analytic_independence_table(table)
        info = null_baseline.analytic_independence_information_metrics(indep_table)
        self.assertAlmostEqual(info["mutual_information"], 0.0, places=9)

    def test_shuffle_preserves_multiset_destroys_order(self):
        selections = [(0,), (1,), (0,), (2,), (1,), (0,)]
        shuffled = null_baseline.shuffled_selection_sequence(selections, seed=1234)
        self.assertEqual(sorted(shuffled), sorted(selections))

    def test_shuffle_is_deterministic_for_fixed_seed(self):
        selections = [(i % 3,) for i in range(50)]
        a = null_baseline.shuffled_selection_sequence(selections, seed=1234)
        b = null_baseline.shuffled_selection_sequence(selections, seed=1234)
        self.assertEqual(a, b)

    def test_different_seed_can_give_different_order(self):
        selections = [(i % 4,) for i in range(50)]
        a = null_baseline.shuffled_selection_sequence(selections, seed=1)
        b = null_baseline.shuffled_selection_sequence(selections, seed=2)
        self.assertNotEqual(a, b)

    def test_layer_null_baseline_is_self_consistent(self):
        selections = [(0,), (1,), (0,), (1,), (0,), (1,)] * 5
        result = null_baseline.layer_null_baseline(0, selections)
        self.assertEqual(result["seed"], null_baseline.NULL_BASELINE_SEED)
        self.assertEqual(result["n_selections"], len(selections))


# ==========================================================================
# bridge.py
# ==========================================================================

class BridgeTests(unittest.TestCase):
    def test_reconcile_identical_marginals_is_compatible(self):
        target_marginal = {0: 0.5, 1: 0.5}
        r2_05_rows = [{"layer_id": 0, "expert_id": 0, "request_fraction_within_layer": 0.5},
                     {"layer_id": 0, "expert_id": 1, "request_fraction_within_layer": 0.5}]
        result = bridge.reconcile_layer_marginal(0, target_marginal, r2_05_rows)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["flagged_experts"], [])

    def test_reconcile_flags_gross_divergence(self):
        target_marginal = {0: 0.95, 1: 0.05}
        r2_05_rows = [{"layer_id": 0, "expert_id": 0, "request_fraction_within_layer": 0.1},
                     {"layer_id": 0, "expert_id": 1, "request_fraction_within_layer": 0.9}]
        result = bridge.reconcile_layer_marginal(0, target_marginal, r2_05_rows)
        self.assertFalse(result["compatible"])
        self.assertIn(0, result["flagged_experts"])

    def test_reconcile_does_not_require_exact_equality(self):
        """Section 13: small, non-gross divergence is NOT flagged."""
        target_marginal = {0: 0.52, 1: 0.48}
        r2_05_rows = [{"layer_id": 0, "expert_id": 0, "request_fraction_within_layer": 0.50},
                     {"layer_id": 0, "expert_id": 1, "request_fraction_within_layer": 0.50}]
        result = bridge.reconcile_layer_marginal(0, target_marginal, r2_05_rows)
        self.assertTrue(result["compatible"])

    def test_load_r2_05_canonical_evidence_none_when_absent(self):
        tmp = Path(tempfile.mkdtemp(prefix="r2-06-bridge-"))
        try:
            old = os.environ.get("ARGINE3_RESEARCH_ROOT")
            os.environ["ARGINE3_RESEARCH_ROOT"] = str(tmp / "evidence")
            try:
                self.assertIsNone(bridge.load_r2_05_canonical_evidence())
            finally:
                if old is None:
                    os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
                else:
                    os.environ["ARGINE3_RESEARCH_ROOT"] = old
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================================
# summary.py / claims.py
# ==========================================================================

class SummaryTests(unittest.TestCase):
    def test_global_summary_strongest_and_weakest_layer_by_nmi(self):
        tables = {
            0: matrix.layer_transition_table(0, {(0, 0): 10, (1, 1): 10}),  # deterministic
            1: matrix.layer_transition_table(1, {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 1}),
        }
        layer_info = info_metrics.all_layer_information_metrics(tables)
        layer_source_rows = {l: concentration.layer_source_concentration_rows(tables[l])
                             for l in tables}
        layer_self = {l: concentration.self_transition_metrics(tables[l]) for l in tables}
        s = summary_mod.global_summary(layer_info, layer_source_rows, layer_self, tables)
        self.assertEqual(s["strongest_layer_by_nmi"], 0)
        self.assertEqual(s["weakest_layer_by_nmi"], 1)

    def test_fraction_layers_with_nmi_positive(self):
        tables = {0: matrix.layer_transition_table(0, {(0, 0): 10, (1, 1): 10}),
                 1: matrix.layer_transition_table(1, {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 1})}
        layer_info = info_metrics.all_layer_information_metrics(tables)
        layer_source_rows = {l: concentration.layer_source_concentration_rows(tables[l])
                             for l in tables}
        layer_self = {l: concentration.self_transition_metrics(tables[l]) for l in tables}
        s = summary_mod.global_summary(layer_info, layer_source_rows, layer_self, tables)
        self.assertAlmostEqual(s["fraction_layers_with_nmi_positive"], 0.5)


class ClaimsTests(unittest.TestCase):
    def test_no_predictor_accuracy_claim_allowed(self):
        bad = {"supported_now": [{"claim": "we can predict with 90% accuracy"}]}
        with self.assertRaises(claims.ForbiddenClaimError):
            claims.check_no_forbidden_claims(bad)

    def test_no_reuse_distance_claim_allowed(self):
        bad = {"supported_now": [{"claim": "this reduces reuse distance"}]}
        with self.assertRaises(claims.ForbiddenClaimError):
            claims.check_no_forbidden_claims(bad)

    def test_not_yet_supported_disclaimers_do_not_trip_the_guard(self):
        payload = {"supported_now": [], "not_yet_supported": [
            {"claim": "we can predict the next expert", "reason": "not measured"}]}
        claims.check_no_forbidden_claims(payload)  # must not raise

    def test_build_claims_produces_valid_safe_claim_set(self):
        tables = {0: matrix.layer_transition_table(0, {(0, 0): 10, (1, 1): 10})}
        layer_info = info_metrics.all_layer_information_metrics(tables)
        layer_source_rows = {0: concentration.layer_source_concentration_rows(tables[0])}
        layer_self = {0: concentration.self_transition_metrics(tables[0])}
        gs = summary_mod.global_summary(layer_info, layer_source_rows, layer_self, tables)
        c = claims.build_claims(gs, layer_self)
        self.assertTrue(c["supported_now"])
        self.assertTrue(c["not_yet_supported"])


# ==========================================================================
# Full pipeline / prerequisite / orchestrator
# ==========================================================================

class _EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-06-"))
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
        """R2-01 -> R2-02 -> R2-03 -> R2-04 -> R2-05 PASS, all real
        orchestrators over a tiny synthetic trace, so R2-06's
        prerequisite binds to a genuine chain with R2-05 PASS."""
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
        return r205


class PrerequisiteTests(_EvidenceCase):
    def test_no_r2_01_run_blocks(self):
        b = prerequisite.require_r2_01_pass_and_r2_05_pass()
        self.assertFalse(b.ok)
        self.assertEqual(b.status, prerequisite.STATUS_R2_01_OR_R2_04_PREREQUISITE_FAILED)

    def test_no_r2_05_run_blocks_even_with_r2_04_pass(self):
        """R2-01/R2-04 PASS alone is not enough -- R2-05 (the first II-B
        experiment) must have reached PASS before R2-06 may run."""
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
        b = prerequisite.require_r2_01_pass_and_r2_05_pass()
        self.assertEqual(b.status, prerequisite.STATUS_NO_R2_05_RUN)
        self.assertFalse(b.ok)

    def test_ok_binding_on_real_full_chain(self):
        self.build_full_passing_chain()
        b = prerequisite.require_r2_01_pass_and_r2_05_pass()
        self.assertTrue(b.ok, b.message)
        self.assertIsNotNone(b.trace_sha256)
        self.assertIsNotNone(b.r2_05_run_id)
        self.assertEqual(b.candidate_status, "CANDIDATE")

    def test_trace_changed_since_r2_01_pass_fails_closed(self):
        self.build_full_passing_chain()
        reg = store.load_registry()
        r201_entry = [e for e in reg if e["identity"]["experiment_id"] == "R2-01"][0]
        trace_path = Path(r201_entry["trace_source"])
        trace_path.write_bytes(trace_path.read_bytes() + b"\x00")
        b = prerequisite.require_r2_01_pass_and_r2_05_pass()
        self.assertFalse(b.ok)

    def test_r2_05_binding_mismatch_fails_closed(self):
        r205 = self.build_full_passing_chain()
        run_dir = Path(r205["run_dir"])
        prov = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
        prov["binding"]["r2_01_run_id"] = "some-other-run-id"
        (run_dir / "provenance.json").write_text(json.dumps(prov), encoding="utf-8")
        b = prerequisite.require_r2_01_pass_and_r2_05_pass()
        self.assertEqual(b.status, prerequisite.STATUS_BINDING_MISMATCH)


class OrchestratorTests(_EvidenceCase):
    def test_full_run_pass(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS", result.get("reasons"))
        self.assertIsNotNone(result["canonical_digest"])
        run_dir = Path(result["run_dir"])
        for fname in ("manifest.json", "provenance.json", "summary.json",
                     "transition_metrics.csv", "layer_transition_summary.csv",
                     "transition_structure.json", "null_baseline.json",
                     "figure_data.json", "claims.json"):
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

    def test_pass_does_not_require_nmi_above_zero(self):
        """PASS does not require detectable transition structure anywhere
        -- a trace with no measurable same-layer structure is valid PASS
        science too."""
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
        # R2-06's own prerequisite (which requires R2-05 PASS) blocks here --
        # confirming the upstream gate, not re-deriving R2-06's own
        # n_experts handling redundantly.
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_PREREQUISITE_NOT_MET)

    def test_prerequisite_not_met_short_circuits_with_no_evidence(self):
        before = len(store.load_registry())
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_PREREQUISITE_NOT_MET)
        after = len(store.load_registry())
        self.assertEqual(before, after)

    def test_bridge_reconciliation_present_in_evidence(self):
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS")
        run_dir = Path(result["run_dir"])
        structure = json.loads((run_dir / "transition_structure.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(structure["bridge_result"])
        self.assertIn("all_layers_compatible", structure["bridge_result"])

    def test_no_model_or_runtime_import_in_r2_06_modules(self):
        import argine.research2.r2_06 as pkg
        import inspect
        import pkgutil
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            mod = __import__(f"argine.research2.r2_06.{mod_info.name}", fromlist=["x"])
            src = inspect.getsource(mod)
            self.assertNotIn("runtime_resolver", src, mod_info.name)
            self.assertNotIn("llama-server", src, mod_info.name)
            self.assertNotIn("llama_server", src, mod_info.name)
            self.assertNotIn("subprocess", src, mod_info.name)

    def test_no_reuse_distance_or_predictor_terms_in_primary_evidence(self):
        """Static check: the words 'precision', 'recall', 'f1' never
        appear in any R2-06 module source (predictor-performance
        vocabulary must never even be introduced)."""
        import argine.research2.r2_06 as pkg
        import inspect
        import pkgutil
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            mod = __import__(f"argine.research2.r2_06.{mod_info.name}", fromlist=["x"])
            src = inspect.getsource(mod).lower()
            for term in ("precision_score", "recall_score", "f1_score"):
                self.assertNotIn(term, src, mod_info.name)

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


class RunnerWiringTests(unittest.TestCase):
    def test_r2_06_registered(self):
        self.assertIn("R2-06", runner.EXPERIMENT_RUN_IMPLEMENTATIONS)
        self.assertIn("R2-06", runner.EXPERIMENT_ANALYSE_IMPLEMENTATIONS)


if __name__ == "__main__":
    unittest.main()
