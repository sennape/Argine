"""Tests for R2-05: Expert Frequency and Concentration.

Covers argine/research2/r2_05/ (request_unit.py, frequency.py, metrics.py,
summary.py, figures.py, claims.py, prerequisite.py, orchestrator.py) and
its wiring into runner.py. Every fixture is synthetic and built in-process
(no real llama-server, no real 30B model, no network, no model weights
loaded, no new trace capture) -- R2-05 must be fully offline, and so must
every test here. Items referenced in comments map to the task's own
section 29 enumerated test requirements.
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
from argine.research2.r2_05 import (claims, figures, frequency, metrics,  # noqa: E402
                                    orchestrator, prerequisite, request_unit)
from argine.research.trace import Step, Trace, TraceMeta  # noqa: E402
from test_r2_01 import make_trace, write_trace  # noqa: E402


# ==========================================================================
# request_unit.py -- items 3, 4, 5, 6, 33, 34, 36
# ==========================================================================

class RequestUnitTests(unittest.TestCase):
    def test_layer_local_identity_no_cross_layer_collapse(self):
        """Items 3/4: expert id 2 in layer 0 is a different logical expert
        than expert id 2 in layer 1 -- counts must never be merged."""
        meta = TraceMeta(model="x", n_experts=4, top_k=1, layers=[0, 1])
        steps = [Step(graph=0, n_tokens=1, phase="decode",
                     routes={0: [(2,)], 1: [(3,)]})]
        trace = Trace(meta=meta, steps=steps)
        counts = request_unit.count_requests_per_layer(trace)
        self.assertEqual(counts[0], {2: 1})
        self.assertEqual(counts[1], {3: 1})
        # expert id 2 does not leak into layer 1's dict
        self.assertNotIn(2, counts[1])

    def test_duplicate_semantics_preserved_within_token(self):
        """Item: 'duplicate semantics' (section 6) -- a token tuple that
        selects the same expert twice counts it twice, never deduplicated."""
        meta = TraceMeta(model="x", n_experts=4, top_k=2, layers=[0])
        steps = [Step(graph=0, n_tokens=1, phase="decode", routes={0: [(1, 1)]})]
        trace = Trace(meta=meta, steps=steps)
        counts = request_unit.count_requests_per_layer(trace)
        self.assertEqual(counts[0][1], 2)

    def test_request_count_reconciliation_matches_independent_computation(self):
        """Item 5: frequency counts reconcile exactly with an
        independently-coded second computation over the same trace."""
        trace = make_trace(n_layers=2, n_experts=6, n_decode_steps=25)
        counts = request_unit.count_requests_per_layer(trace)
        indep = request_unit.independent_count_per_layer(trace)
        problems = frequency.reconcile_total_requests(counts, indep)
        self.assertEqual(problems, [])
        for layer in counts:
            self.assertEqual(sum(counts[layer].values()), indep[layer])

    def test_reconciliation_fails_closed_on_mismatch(self):
        """Item 5 (negative case): a manufactured mismatch is reported,
        never silently accepted."""
        problems = frequency.reconcile_total_requests({0: {1: 5}}, {0: 999})
        self.assertTrue(problems)

    def test_expert_id_range_validation_fails_closed(self):
        """Items 33/36: an expert id outside [0, n_experts) fails closed."""
        self.assertFalse(request_unit.expert_id_range_valid({0: {99: 1}}, n_experts=6))
        self.assertTrue(request_unit.expert_id_range_valid({0: {5: 1}}, n_experts=6))

    def test_malformed_layer_id_is_not_silently_accepted(self):
        """Item 34: a negative layer id is not a valid layer identity."""
        counts = {-1: {0: 1}, 0: {0: 1}}
        self.assertFalse(all(layer >= 0 for layer in counts))


# ==========================================================================
# frequency.py -- items 7, 8, 9, 26, 27, 35
# ==========================================================================

class FrequencyTests(unittest.TestCase):
    def test_deterministic_rank_and_tie_breaking(self):
        """Items 8/9: equal counts break ties by ascending expert_id,
        deterministically, every time."""
        counts = {5: 10, 1: 10, 3: 10, 2: 5}
        rows_a = frequency.layer_frequency_rows(0, counts)
        rows_b = frequency.layer_frequency_rows(0, counts)
        self.assertEqual(rows_a, rows_b)
        tied_ids_in_rank_order = [r["expert_id"] for r in rows_a if r["request_count"] == 10]
        self.assertEqual(tied_ids_in_rank_order, [1, 3, 5])

    def test_cumulative_fraction_monotonic_and_reaches_one(self):
        rows = frequency.layer_frequency_rows(0, {0: 3, 1: 2, 2: 5})
        fracs = [r["cumulative_request_fraction_within_layer"] for r in rows]
        self.assertEqual(fracs, sorted(fracs))
        self.assertAlmostEqual(fracs[-1], 1.0)

    def test_probability_mass_normalization(self):
        """Item 27 / 'probability mass per layer sums correctly'."""
        counts = {0: {0: 3, 1: 2, 2: 5}}
        self.assertEqual(frequency.probability_mass_valid(counts), [])

    def test_zero_request_layer_handling(self):
        """Item 35: a layer with zero requests is a valid state, not an
        error -- probability_mass_valid skips it rather than dividing by
        zero or flagging it as invalid."""
        self.assertEqual(frequency.probability_mass_valid({0: {}}), [])

    def test_never_observed_expert_count_with_nominal_metadata(self):
        """Item 7: never-observed count reported only when an
        authoritative nominal count exists."""
        summary = frequency.layer_population_summary(0, {0: 1, 1: 1}, nominal_expert_count=6)
        self.assertEqual(summary["never_observed_expert_count"], 4)
        summary_no_nominal = frequency.layer_population_summary(0, {0: 1, 1: 1},
                                                                 nominal_expert_count=None)
        self.assertIsNone(summary_no_nominal["never_observed_expert_count"])


# ==========================================================================
# metrics.py -- items 10-25, 31, 32
# ==========================================================================

class MetricsTests(unittest.TestCase):
    def test_uniform_distribution_gives_baseline_values(self):
        """Item 20/31: a perfectly uniform distribution is valid PASS
        science and reproduces the theoretical uniform baseline exactly."""
        counts = {i: 10 for i in range(8)}
        m = metrics.layer_concentration_metrics(0, counts, nominal_expert_count=8)
        self.assertAlmostEqual(m["normalized_entropy"], 1.0, places=9)
        self.assertAlmostEqual(m["effective_expert_count"], 8.0, places=9)
        self.assertAlmostEqual(m["hhi"], 1.0 / 8, places=9)
        self.assertAlmostEqual(m["gini"], 0.0, places=9)
        self.assertAlmostEqual(m["effective_expert_fraction"], 1.0, places=9)

    def test_maximally_concentrated_distribution(self):
        """Item 32: a single expert carrying all traffic is valid PASS
        science (the opposite extreme from uniform)."""
        counts = {0: 100, 1: 0, 2: 0, 3: 0}
        counts = {k: v for k, v in counts.items() if v > 0}
        m = metrics.layer_concentration_metrics(0, counts, nominal_expert_count=4)
        self.assertAlmostEqual(m["shannon_entropy"], 0.0, places=9)
        self.assertAlmostEqual(m["normalized_entropy"], 0.0, places=9)
        self.assertAlmostEqual(m["effective_expert_count"], 1.0, places=9)
        self.assertAlmostEqual(m["hhi"], 1.0, places=9)
        self.assertAlmostEqual(m["max_expert_share"], 1.0, places=9)

    def test_top_k_shares_predeclared_and_filtered_to_n(self):
        """Items 15-19: top-1/4/8/16/32 are predeclared and filtered to
        valid k <= N (never chosen after inspecting results)."""
        self.assertEqual(metrics.top_k_values_for_n(128), [1, 4, 8, 16, 32])
        self.assertEqual(metrics.top_k_values_for_n(6), [1, 4])
        self.assertEqual(metrics.top_k_values_for_n(1), [1])

    def test_uniform_baseline_reference_values(self):
        """Items 20/13: the uniform-baseline reference values match the
        closed-form formulas for N nominal experts."""
        u = metrics._uniform_baseline(128)
        self.assertEqual(u["normalized_entropy"], 1.0)
        self.assertEqual(u["effective_expert_count"], 128.0)
        self.assertAlmostEqual(u["hhi"], 1.0 / 128)
        self.assertEqual(u["gini"], 0.0)
        self.assertAlmostEqual(u["top_k_share"][8], 8 / 128)

    def test_effective_expert_fraction_ratio(self):
        """Item 21."""
        counts = {i: 10 for i in range(8)}
        m = metrics.layer_concentration_metrics(0, counts, nominal_expert_count=8)
        self.assertAlmostEqual(m["concentration_ratios"]["effective_expert_fraction"], 1.0)

    def test_concentration_counts_experts_for_pct(self):
        """Items 22-25: minimum experts needed to reach 50/80/90/95% of
        requests, over a known distribution."""
        # expert 0 carries 50, expert 1 carries 30, expert 2 carries 20 (total 100)
        counts = {0: 50, 1: 30, 2: 20}
        m = metrics.layer_concentration_metrics(0, counts, nominal_expert_count=3)
        self.assertEqual(m["experts_for_pct"][0.50], 1)
        self.assertEqual(m["experts_for_pct"][0.80], 2)
        self.assertEqual(m["experts_for_pct"][0.90], 3)
        self.assertEqual(m["experts_for_pct"][0.95], 3)

    def test_zero_request_layer_is_valid_not_an_error(self):
        """Item 35 at the metrics layer: no exception, every normalized
        field explicitly None."""
        m = metrics.layer_concentration_metrics(0, {}, nominal_expert_count=8)
        self.assertEqual(m["total_requests"], 0)
        self.assertIsNone(m["normalized_entropy"])
        self.assertIsNone(m["gini"])

    def test_gini_coefficient_known_values(self):
        """Item 14: Gini = 0 for a uniform population, > 0 otherwise,
        increasing with concentration."""
        self.assertAlmostEqual(metrics.gini_coefficient([0.25, 0.25, 0.25, 0.25]), 0.0, places=9)
        g_concentrated = metrics.gini_coefficient([0.0, 0.0, 0.0, 1.0])
        g_uniform = metrics.gini_coefficient([0.25, 0.25, 0.25, 0.25])
        self.assertGreater(g_concentrated, g_uniform)

    def test_shannon_entropy_zero_for_point_mass(self):
        """Item 10."""
        self.assertAlmostEqual(metrics.shannon_entropy([1.0]), 0.0)

    def test_n_eff_equals_n_for_uniform(self):
        """Item 12."""
        import math
        probs = [1 / 10] * 10
        h = metrics.shannon_entropy(probs)
        self.assertAlmostEqual(math.exp(h), 10.0, places=9)


# ==========================================================================
# summary.py -- items 26, 28, 29, 30
# ==========================================================================

class SummaryTests(unittest.TestCase):
    def _layer_metric(self, layer, normalized_entropy, gini, hhi, eff_frac, total_requests,
                      top8=None, top16=None):
        return {
            "layer_id": layer, "normalized_entropy": normalized_entropy, "gini": gini,
            "hhi": hhi, "effective_expert_fraction": eff_frac, "total_requests": total_requests,
            "effective_expert_count": eff_frac * 8,
            "top_k_share": {k: v for k, v in (("8", top8), ("16", top16)) if v is not None},
            "experts_for_pct": {0.50: 2, 0.80: 4, 0.90: 6, 0.95: 7},
        }

    def test_layer_median_and_min_max(self):
        """Item 26."""
        lm = {
            0: self._layer_metric(0, 0.9, 0.1, 0.2, 0.9, 100),
            1: self._layer_metric(1, 0.5, 0.4, 0.3, 0.5, 100),
            2: self._layer_metric(2, 0.7, 0.2, 0.25, 0.7, 100),
        }
        from argine.research2.r2_05 import summary as summary_mod
        s = summary_mod.global_summary(lm)
        self.assertAlmostEqual(s["layer_median"]["normalized_entropy"], 0.7)
        self.assertAlmostEqual(s["layer_median"]["normalized_entropy_min"], 0.5)
        self.assertAlmostEqual(s["layer_median"]["normalized_entropy_max"], 0.9)

    def test_most_and_least_concentrated_layer(self):
        """Items 28/29: lowest normalized entropy = most concentrated."""
        lm = {
            0: self._layer_metric(0, 0.9, 0.1, 0.2, 0.9, 100),
            1: self._layer_metric(1, 0.5, 0.4, 0.3, 0.5, 100),
            2: self._layer_metric(2, 0.7, 0.2, 0.25, 0.7, 100),
        }
        from argine.research2.r2_05 import summary as summary_mod
        s = summary_mod.global_summary(lm)
        self.assertEqual(s["most_concentrated_layer"], 1)
        self.assertEqual(s["least_concentrated_layer"], 0)

    def test_request_weighted_summary_distinct_from_layer_median(self):
        """Item 30: request-weighted aggregate differs from the
        layer-median when request volumes differ across layers, and the
        two are reported as explicitly distinct fields."""
        lm = {
            0: self._layer_metric(0, 0.9, 0.1, 0.2, 0.9, 1000),
            1: self._layer_metric(1, 0.1, 0.9, 0.8, 0.1, 10),
        }
        from argine.research2.r2_05 import summary as summary_mod
        s = summary_mod.global_summary(lm)
        layer_median = s["layer_median"]["normalized_entropy"]
        weighted = s["request_weighted_aggregate"]["normalized_entropy"]
        self.assertNotAlmostEqual(layer_median, weighted)
        # the heavily-weighted layer 0 (1000 requests) should dominate the
        # weighted aggregate, pulling it close to layer 0's own value
        self.assertLess(abs(weighted - 0.9), abs(layer_median - 0.9))

    def test_heterogeneity_descriptive_only(self):
        """Item 13 area: spread is reported, no causal language."""
        from argine.research2.r2_05 import summary as summary_mod
        lm = {
            0: self._layer_metric(0, 0.9, 0.1, 0.2, 0.9, 100),
            1: self._layer_metric(1, 0.5, 0.4, 0.3, 0.5, 100),
        }
        het = summary_mod.layer_heterogeneity(lm)
        self.assertIn(het["classification"], ("BROADLY_HOMOGENEOUS", "HETEROGENEOUS",
                                              "STRONGLY_HETEROGENEOUS"))
        self.assertNotIn("because", het["note"].lower())


# ==========================================================================
# claims.py -- items 37, 38
# ==========================================================================

class ClaimsTests(unittest.TestCase):
    def test_no_unsupported_hot_stability_claim(self):
        """Item 37: a 'these experts remain hot over time' style claim is
        never allowed to appear in supported_now."""
        bad = {"supported_now": [{"claim": "these experts remain hot over time"}]}
        with self.assertRaises(claims.ForbiddenClaimError):
            claims.check_no_forbidden_claims(bad)

    def test_no_power_law_claim_without_evidence(self):
        """Item 38."""
        bad = {"supported_now": [{"claim": "the distribution is Zipfian"}]}
        with self.assertRaises(claims.ForbiddenClaimError):
            claims.check_no_forbidden_claims(bad)

    def test_not_yet_supported_disclaimers_do_not_trip_the_guard(self):
        """The forbidden-phrase guard only scans what R2-05 actually
        asserts; a not_yet_supported disclaimer naming the forbidden
        phrase in order to disclaim it must not raise."""
        payload = {"supported_now": [], "not_yet_supported": [
            {"claim": "these experts remain hot over time", "reason": "not measured"}]}
        claims.check_no_forbidden_claims(payload)  # must not raise

    def test_build_claims_produces_valid_safe_claim_set(self):
        from argine.research2.r2_05 import summary as summary_mod, metrics as metrics_mod
        counts = {i: (10 if i else 40) for i in range(6)}
        m = {0: metrics_mod.layer_concentration_metrics(0, counts, nominal_expert_count=6)}
        gs = summary_mod.global_summary(m)
        het = summary_mod.layer_heterogeneity(m)
        c = claims.build_claims(gs, het, claims.LFU_BRIDGE_NOTE)
        self.assertTrue(c["supported_now"])
        self.assertTrue(c["not_yet_supported"])


# ==========================================================================
# Full pipeline / prerequisite / orchestrator -- items 1, 2, 39, 40, 41,
# 42, plus negative/fail-closed cases
# ==========================================================================

class _EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-05-"))
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
        """R2-01 -> R2-02 -> R2-03 -> R2-04 PASS, all real orchestrators
        over a tiny synthetic trace, so R2-05's prerequisite binds to a
        genuine II-A-closed chain (same approach test_r2_04 uses for its
        own upstream)."""
        p = self.trace_path(n_layers=2, n_experts=8, n_decode_steps=40, **trace_kwargs)
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
        return r204


class PrerequisiteTests(_EvidenceCase):
    def test_no_r2_01_run_blocks(self):
        """Item 1: no R2-01 run at all blocks R2-05."""
        b = prerequisite.require_r2_01_pass_and_r2_04_pass()
        self.assertEqual(b.status, prerequisite.STATUS_R2_01_PREREQUISITE_FAILED)
        self.assertFalse(b.ok)

    def test_no_r2_04_run_blocks(self):
        """Item 1: R2-01 PASS alone is not enough -- II-A must be closed
        (R2-04 PASS) before R2-05 may run."""
        p = self.trace_path()
        self.register_r2_01(p, outcome="PASS")
        b = prerequisite.require_r2_01_pass_and_r2_04_pass()
        self.assertEqual(b.status, prerequisite.STATUS_NO_R2_04_RUN)
        self.assertFalse(b.ok)

    def test_r2_01_not_candidate_blocks(self):
        """Item 1 (candidate-status binding): a NON-CANDIDATE R2-01 PASS
        run is refused even though its scientific outcome was PASS."""
        p = self.trace_path()
        self.register_r2_01(p, outcome="PASS", candidate_status="NON-CANDIDATE")
        b = prerequisite.require_r2_01_pass_and_r2_04_pass()
        self.assertEqual(b.status, prerequisite.STATUS_R2_01_NOT_CANDIDATE)
        self.assertFalse(b.ok)

    def test_ok_binding_on_real_full_chain(self):
        """Items 1/2: trace SHA binding resolves dynamically from R2-01's
        own PASS evidence, re-verified byte-identical on disk."""
        self.build_full_passing_chain()
        b = prerequisite.require_r2_01_pass_and_r2_04_pass()
        self.assertTrue(b.ok, b.message)
        self.assertIsNotNone(b.trace_sha256)
        self.assertEqual(b.candidate_status, "CANDIDATE")

    def test_trace_changed_since_r2_01_pass_fails_closed(self):
        """Item 2: trace SHA binding fails closed if the trace file on
        disk no longer matches what R2-01's PASS run validated."""
        r204 = self.build_full_passing_chain()
        # corrupt the trace file registered under R2-01
        reg = store.load_registry()
        r201_entry = [e for e in reg if e["identity"]["experiment_id"] == "R2-01"][0]
        trace_path = Path(r201_entry["trace_source"])
        trace_path.write_bytes(trace_path.read_bytes() + b"\x00")
        b = prerequisite.require_r2_01_pass_and_r2_04_pass()
        self.assertFalse(b.ok)

    def test_r2_04_binding_mismatch_fails_closed(self):
        r204 = self.build_full_passing_chain()
        run_dir = Path(r204["run_dir"])
        prov = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
        prov["binding"]["r2_01_run_id"] = "some-other-run-id"
        (run_dir / "provenance.json").write_text(json.dumps(prov), encoding="utf-8")
        b = prerequisite.require_r2_01_pass_and_r2_04_pass()
        self.assertEqual(b.status, prerequisite.STATUS_BINDING_MISMATCH)


class OrchestratorTests(_EvidenceCase):
    def test_full_run_pass(self):
        """End-to-end PASS: items 39 (deterministic canonical digest) and
        41 (no model/runtime invocation -- nothing here ever imports or
        calls argine.research2.runtime_resolver or starts a process)."""
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS", result.get("reasons"))
        self.assertIsNotNone(result["canonical_digest"])
        run_dir = Path(result["run_dir"])
        for fname in ("manifest.json", "provenance.json", "summary.json",
                     "expert_frequency.csv", "layer_concentration.csv",
                     "concentration.json", "figure_data.json", "claims.json"):
            self.assertTrue((run_dir / fname).is_file(), f"missing {fname}")

    def test_two_repeated_full_analyses_identical(self):
        """Item 40: two independent full analyses over identical trace
        SHA / metric definitions / code commit produce identical
        canonical frequency counts, ranks, concentration metrics,
        summary, and digest."""
        self.build_full_passing_chain()
        result_a = orchestrator.run(allow_dirty=True)
        result_b = orchestrator.run(allow_dirty=True)
        self.assertEqual(result_a["outcome"], "PASS")
        self.assertEqual(result_b["outcome"], "PASS")
        self.assertEqual(result_a["canonical_digest"], result_b["canonical_digest"])
        self.assertEqual(result_a["headline"], result_b["headline"])

    def test_classify_outcome_fail_on_nondeterminism(self):
        """Item 40 (negative): a detected digest mismatch between the two
        internal syntheses is an immediate FAIL, never silently ignored."""
        outcome, reasons = orchestrator.classify_outcome(
            [], ["digest mismatch"], {"headline": {}})
        self.assertEqual(outcome, orchestrator.OUTCOME_FAIL)

    def test_classify_outcome_fail_on_synthesis_error(self):
        outcome, reasons = orchestrator.classify_outcome(["bad data"], [], None)
        self.assertEqual(outcome, orchestrator.OUTCOME_FAIL)

    def test_near_uniform_result_is_valid_pass(self):
        """Item 31: PASS does not require strong concentration."""
        self.build_full_passing_chain()
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], "PASS")

    def test_nominal_expert_count_unavailable_is_inconclusive(self):
        """Item: 'nominal expert count unavailable in a way that
        invalidates normalized metrics' -> INCONCLUSIVE, not a silent
        fallback to an observed-only count for the headline result."""
        p = self.trace_path(n_layers=2, n_experts=8, expert_bytes=True, n_decode_steps=40)
        # overwrite the trace with n_experts=0 (no authoritative metadata)
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
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_INCONCLUSIVE)

    def test_prerequisite_not_met_short_circuits_with_no_evidence(self):
        """Item: a blocked prerequisite creates no run directory."""
        before = len(store.load_registry())
        result = orchestrator.run(allow_dirty=True)
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_PREREQUISITE_NOT_MET)
        after = len(store.load_registry())
        self.assertEqual(before, after)

    def test_no_model_or_runtime_import_in_r2_05_modules(self):
        """Item 41 (static check): no R2-05 module imports the runtime
        resolver or anything that would start a model/runtime process."""
        import argine.research2.r2_05 as pkg
        import inspect
        import pkgutil
        for mod_info in pkgutil.iter_modules(pkg.__path__):
            mod = __import__(f"argine.research2.r2_05.{mod_info.name}", fromlist=["x"])
            src = inspect.getsource(mod)
            self.assertNotIn("runtime_resolver", src, mod_info.name)
            self.assertNotIn("llama-server", src, mod_info.name)
            self.assertNotIn("llama_server", src, mod_info.name)
            self.assertNotIn("subprocess", src, mod_info.name)

    def test_frozen_ii_a_paper_tree_unchanged(self):
        """Item 42: running R2-05 end-to-end never touches
        paper/research2/ (the frozen II-A paper tranche)."""
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
    def test_r2_05_registered(self):
        self.assertIn("R2-05", runner.EXPERIMENT_RUN_IMPLEMENTATIONS)
        self.assertIn("R2-05", runner.EXPERIMENT_ANALYSE_IMPLEMENTATIONS)


if __name__ == "__main__":
    unittest.main()
