"""Tests for R2-08: Routing Drift and Workload Dependence.

Synthetic fixtures only: no real llama-server, no real 30B model, no network,
no new trace capture. Tests use temp evidence roots and injectable capture
functions.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Tuple
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from argine.research.trace import Step, Trace, TraceMeta  # noqa: E402
from argine.research2 import runner, store  # noqa: E402
from argine.research2.r2_08 import (  # noqa: E402
    bridges, capture, capture_matrix, claims, drift, figures, matched_horizon,
    orchestrator, prerequisite, summary, variability, windowing, workloads,
)


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_trace(trace: Trace, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"format": "argine-router-trace", "version": 1,
                             "meta": trace.meta.to_json()}) + "\n")
        for st in trace.steps:
            fh.write(json.dumps(st.to_json()) + "\n")
    return _sha256_bytes(path.read_bytes())


def make_decode_trace(*, n_layers: int = 2, n_experts: int = 128, top_k: int = 2,
                      n_decode: int = 300, seed: int = 0, phase_bias: float = 0.0,
                      workload: str = "synthetic", with_prefill: bool = True) -> Trace:
    import random
    rng = random.Random(int(hashlib.sha256(f"{seed}|{n_layers}|{n_experts}|{n_decode}|{workload}|{phase_bias}".encode()).hexdigest()[:16], 16))
    meta = TraceMeta(model="synthetic-qwen3-30b-a3b", n_experts=n_experts, top_k=top_k,
                     layers=list(range(n_layers)),
                     expert_bytes={l: 1024 for l in range(n_layers)},
                     workload=workload, split="dev", source="test_r2_08")
    steps = []
    g = 0
    if with_prefill:
        steps.append(Step(graph=g, n_tokens=32, phase="prefill", segment=workload,
                          routes={l: [tuple(rng.sample(range(n_experts), top_k)) for _ in range(32)]
                                  for l in range(n_layers)}))
        g += 1
    for i in range(n_decode):
        routes = {}
        for l in range(n_layers):
            # introduce controllable drift/workload dependence via bias
            base = int((phase_bias * 20 + l * 3 + i * 0.05) % max(1, n_experts - top_k))
            choices = []
            for _ in range(top_k):
                if rng.random() < 0.6:
                    choices.append((base + rng.randrange(0, min(16, n_experts))) % n_experts)
                else:
                    choices.append(rng.randrange(n_experts))
            # unique
            seen = []
            for c in choices:
                if c not in seen:
                    seen.append(c)
            while len(seen) < top_k:
                c = rng.randrange(n_experts)
                if c not in seen:
                    seen.append(c)
            routes[l] = [tuple(seen[:top_k])]
        steps.append(Step(graph=g, n_tokens=1, phase="decode", segment=workload, routes=routes))
        g += 1
    return Trace(meta=meta, steps=steps)


class TempEvidenceMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-08-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._env_patch = mock.patch.dict(os.environ, {"ARGINE3_RESEARCH_ROOT": str(self.tmp)})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)


class DriftMathTests(unittest.TestCase):
    def test_jsd_identical_is_zero(self):
        p = [0.25, 0.25, 0.25, 0.25] + [0.0] * 124
        self.assertAlmostEqual(drift.jsd(p, p), 0.0, places=12)

    def test_jsd_symmetry(self):
        p = drift.normalize_distribution({0: 10, 1: 5, 2: 1})
        q = drift.normalize_distribution({0: 1, 1: 8, 2: 7})
        self.assertAlmostEqual(drift.jsd(p, q), drift.jsd(q, p), places=12)

    def test_jsd_in_range(self):
        p = drift.normalize_distribution({i: i + 1 for i in range(20)})
        q = drift.normalize_distribution({i: 21 - i for i in range(20)})
        v = drift.jsd(p, q)
        self.assertGreaterEqual(v, 0.0)
        self.assertLessEqual(v, 1.0)

    def test_jsd_known_fixture_disjoint(self):
        # two disjoint one-hot distributions -> JSD = 1 (log2)
        p = [1.0] + [0.0] * 127
        q = [0.0, 1.0] + [0.0] * 126
        self.assertAlmostEqual(drift.jsd(p, q), 1.0, places=10)

    def test_probability_normalization(self):
        p = drift.normalize_distribution({0: 3, 5: 1, 127: 4})
        self.assertTrue(drift.is_valid_probability_vector(p))
        self.assertEqual(len(p), 128)
        self.assertAlmostEqual(sum(p), 1.0)

    def test_top8_jaccard_tie_breaking_deterministic(self):
        c1 = {i: 1 for i in range(16)}  # all tied
        c2 = {i: 1 for i in range(16)}
        t1 = drift.top_k_experts(c1, 8)
        t2 = drift.top_k_experts(c2, 8)
        self.assertEqual(t1, list(range(8)))
        self.assertEqual(t1, t2)

    def test_jaccard_distance(self):
        self.assertAlmostEqual(drift.jaccard_distance([1, 2, 3, 4], [1, 2, 3, 4]), 0.0)
        self.assertAlmostEqual(drift.jaccard_distance([1, 2], [3, 4]), 1.0)
        self.assertAlmostEqual(drift.jaccard_distance([1, 2, 3, 4], [1, 2, 5, 6]), 1 - 2 / 6)

    def test_normalized_entropy_full_support(self):
        p = drift.normalize_distribution({i: 1 for i in range(128)})
        self.assertAlmostEqual(drift.normalized_entropy(p, 128), 1.0, places=10)
        q = [1.0] + [0.0] * 127
        self.assertAlmostEqual(drift.normalized_entropy(q, 128), 0.0, places=10)


class HorizonWindowTests(unittest.TestCase):
    def test_matched_horizon_capped_at_512(self):
        infos = [matched_horizon.CaptureHorizonInfo("w", 42, "h", 1000, 0, 800, 800) for _ in range(8)]
        h = matched_horizon.compute_matched_horizon(infos)
        self.assertTrue(h["ok"])
        self.assertEqual(h["h"], 512)

    def test_matched_horizon_minimum_across_captures(self):
        infos = [matched_horizon.CaptureHorizonInfo("w", i, "h", 500, 0, 400, 400) for i in range(7)]
        infos.append(matched_horizon.CaptureHorizonInfo("w", 99, "h", 400, 0, 300, 300))
        h = matched_horizon.compute_matched_horizon(infos)
        self.assertEqual(h["h"], 300)

    def test_matched_horizon_lt_256_inconclusive(self):
        infos = [matched_horizon.CaptureHorizonInfo("w", i, "h", 300, 0, 200, 200) for i in range(8)]
        h = matched_horizon.compute_matched_horizon(infos)
        self.assertFalse(h["ok"])
        self.assertIn("256", h["reason"])

    def test_exactly_8_windows_and_remainder(self):
        wins, defn = windowing.split_into_windows(list(range(260)), 260)
        self.assertEqual(len(wins), 8)
        self.assertEqual(defn["n_windows"], 8)
        self.assertEqual(defn["window_length"], 32)
        self.assertEqual(defn["positions_trimmed"], 4)
        self.assertEqual(sum(len(w) for w in wins), 256)

    def test_decode_only_extraction(self):
        trace = make_decode_trace(n_decode=100, with_prefill=True)
        sels = orchestrator._decode_selections(trace, 0, 50)
        self.assertEqual(len(sels), 50)
        # all from decode token_selections
        total_decode = sum(1 for _ in trace.token_selections(0, phase="decode"))
        self.assertEqual(total_decode, 100)


class VariabilityTests(unittest.TestCase):
    def test_replicate_vs_cross_workload_ratios(self):
        nominal = 128
        def dist(seed):
            return drift.normalize_distribution({seed % 10 + i: 10 - i for i in range(5)}, nominal)
        by_layer_dist = {0: {42: dist(42), 43: dist(43)}}
        by_layer_count = {0: {42: {42 % 10: 1}, 43: {43 % 10: 1}}}
        rep = variability.replicate_variability_for_workload(by_layer_dist, by_layer_count)
        self.assertIsNotNone(rep["median_replicate_jsd"])
        self.assertIn(0, rep["per_layer"])

    def test_cross_workload_preserves_replicate_identity(self):
        nominal = 128
        ld = {}
        lc = {}
        for wid in workloads.CANONICAL_WORKLOAD_IDS:
            for s in (42, 43):
                d = drift.normalize_distribution({(hash(wid) + s + i) % nominal: 5 for i in range(8)}, nominal)
                ld[(wid, s)] = {0: d}
                lc[(wid, s)] = {0: {(hash(wid) + s) % nominal: 5}}
        cross = variability.cross_workload_variability(ld, lc)
        self.assertTrue(any(p["seed_a"] != p["seed_b"] or p["workload_a"] != p["workload_b"]
                            for p in cross["pairs"]))
        # raw replicate identity preserved in pairs
        for p in cross["pairs"]:
            self.assertIn("seed_a", p)
            self.assertIn("seed_b", p)

    def test_zero_denominator_ratio_handling(self):
        r = summary.safe_ratio(1.0, 0.0)
        self.assertIsNone(r)
        reg = summary.classify_regime(None, 0.1, 0.2)
        self.assertEqual(reg["regime"], summary.REGIME_MIXED)


class RegimeTests(unittest.TestCase):
    def test_workload_dominant(self):
        reg = summary.classify_regime(0.02, 0.02, 0.10)
        self.assertEqual(reg["regime"], summary.REGIME_WORKLOAD)

    def test_temporal_dominant(self):
        reg = summary.classify_regime(0.10, 0.02, 0.02)
        self.assertEqual(reg["regime"], summary.REGIME_TEMPORAL)

    def test_replicate_dominant(self):
        reg = summary.classify_regime(0.02, 0.10, 0.02)
        self.assertEqual(reg["regime"], summary.REGIME_REPLICATE)

    def test_stable(self):
        reg = summary.classify_regime(0.01, 0.01, 0.01)
        self.assertEqual(reg["regime"], summary.REGIME_STABLE)

    def test_mixed(self):
        reg = summary.classify_regime(0.04, 0.05, 0.06)
        self.assertEqual(reg["regime"], summary.REGIME_MIXED)

    def test_thresholds_predeclared(self):
        t = summary.thresholds_json()
        self.assertTrue(t["predeclared_before_real_execution"])
        self.assertIn("dominance_ratio", t)
        self.assertIn("stable_low_drift_jsd", t)


class ClaimsTests(unittest.TestCase):
    def test_forbidden_claim_rejected(self):
        with self.assertRaises(claims.ForbiddenClaimError):
            claims.check_no_forbidden_claims({"supported_now": [{"claim": "This proves all MoE models generally behave this way."}]})

    def test_disclaimers_not_scanned(self):
        claims.check_no_forbidden_claims({
            "supported_now": [{"claim": "Routing distributions exhibit measurable temporal drift."}],
            "not_yet_supported": [{"claim": "This proves all MoE models behave this way.", "reason": "forbidden"}],
        })


class WorkloadTests(TempEvidenceMixin):
    def test_exactly_four_families(self):
        self.assertEqual(workloads.CANONICAL_WORKLOAD_IDS,
                         ["coding-v1", "reasoning-v1", "summarization-v1", "instruction-v1"])

    def test_eight_cells(self):
        self.assertEqual(len(workloads.CANONICAL_CELLS), 8)
        self.assertEqual(len(set(workloads.CANONICAL_CELLS)), 8)

    def test_workload_immutability_shas(self):
        m = workloads.workload_manifest_json()
        for w in m["workloads"]:
            p = REPO_ROOT / w["source_path"]
            self.assertTrue(p.exists(), w["source_path"])
            actual = _sha256_bytes(p.read_bytes())
            self.assertEqual(actual, w["sha256"])

    def test_coding_reused_cell(self):
        self.assertEqual(workloads.REUSED_CELL, ("coding-v1", 42))


class CaptureMatrixTests(TempEvidenceMixin):
    def test_missing_detection(self):
        cells = {}
        missing = capture_matrix.missing_or_invalid_cells(cells)
        self.assertEqual(len(missing), 8)

    def test_resumable_and_persisted(self):
        c = capture_matrix.CaptureCell(
            workload_id="coding-v1", workload_version="v1", seed=42, prompt_sha256="p",
            model_sha256="m", runtime_sha256="r", configuration_fingerprint="f",
            configuration={}, validation_state="VALID", canonical_status=True)
        capture_matrix.upsert_cell(c)
        loaded = capture_matrix.load_matrix()
        self.assertIn(("coding-v1", 42), loaded)

    def test_failed_capture_never_canonical(self):
        c = capture_matrix.CaptureCell(
            workload_id="reasoning-v1", workload_version="r2-08-v1", seed=42, prompt_sha256="p",
            model_sha256="m", runtime_sha256="r", configuration_fingerprint="f",
            configuration={}, validation_state="INVALID", canonical_status=False)
        capture_matrix.upsert_cell(c)
        self.assertFalse(capture_matrix.all_canonical_valid(capture_matrix.load_matrix()))

    def test_config_fingerprint_only_workload_seed_may_differ(self):
        base = capture_matrix.configuration_fingerprint_dict(
            model_sha256="m", runtime_sha256="r", model_name="x", runtime_args={},
            expert_budget="4G", ctx=4096, threads=4, threads_batch=12, sampling={},
            trace_schema="s", instrumentation={}, workload_id="coding-v1",
            workload_version="v1", workload_sha256="w1", seed=42, prompt_sha256="p1")
        other = dict(base)
        other.update({"workload_id": "reasoning-v1", "workload_version": "r2-08-v1",
                      "workload_sha256": "w2", "seed": 43, "prompt_sha256": "p2"})
        res = capture_matrix.compare_fingerprint_compatibility([base, other])
        self.assertTrue(res["compatible"])
        bad = dict(base); bad["ctx"] = 2048
        res2 = capture_matrix.compare_fingerprint_compatibility([base, bad])
        self.assertFalse(res2["compatible"])


class OrchestratorTests(TempEvidenceMixin):
    def _make_reused_trace(self):
        tr = make_decode_trace(n_decode=300, seed=42, workload="coding-v1")
        path = self.tmp / "reused-r2-01" / "trace.jsonl.gz"
        sha = write_trace(tr, path)
        return path, sha

    def _binding(self, trace_path, trace_sha):
        return prerequisite.R208Binding(
            status=prerequisite.STATUS_OK, message="ok",
            r2_01_run_id=prerequisite.REUSED_R2_01_RUN_ID,
            r2_04_run_id="r204", r2_05_run_id="r205", r2_06_run_id="r206", r2_07_run_id="r207",
            reused_trace_path=str(trace_path), reused_trace_sha256=trace_sha,
            reused_capture_source=prerequisite.REUSED_CAPTURE_SOURCE, candidate_status="CANDIDATE",
        )

    def _fake_capture(self, created, n_decode=300):
        def fn(model_path, workload_id, seed, out_dir, allow_dirty=False, runtime_path=None, dry_run=False, **kw):
            tr = make_decode_trace(n_decode=n_decode, seed=seed * 10 + (0 if "coding" in workload_id else hash(workload_id) % 7),
                                   workload=workload_id, phase_bias=(hash(workload_id) + seed) % 5)
            tp = Path(out_dir) / "run-00-4G" / "trace.jsonl.gz"
            sha = write_trace(tr, tp)
            created[(workload_id, seed)] = created.get((workload_id, seed), 0) + 1
            return {"outcome": "CAPTURED", "trace_path": str(tp),
                    "validation": {"ok": True, "trace_sha256": sha, "size_bytes": tp.stat().st_size,
                                   "trace_meta": {"fingerprint": {"model_sha256": "msha", "runtime_sha256": "rsha"}}},
                    "out_dir": str(out_dir), "run_id": f"cap-{workload_id}-{seed}"}
        return fn

    def _mock_execute_capture(self, calls, n_decode=300):
        """Mock for capture_mod.execute_capture (default dispatch path).
        Captures call args/kwargs for regression assertions; never spawns a process."""
        def fn(**kwargs):
            calls.append(dict(kwargs))
            workload_id = kwargs["workload_id"]
            seed = kwargs["seed"]
            out_dir = Path(kwargs["out_dir"])
            tr = make_decode_trace(n_decode=n_decode, seed=seed * 10 + (0 if "coding" in workload_id else hash(workload_id) % 7),
                                   workload=workload_id, phase_bias=(hash(workload_id) + seed) % 5)
            tp = out_dir / "run-00-4G" / "trace.jsonl.gz"
            sha = write_trace(tr, tp)
            return {"outcome": "CAPTURED", "trace_path": str(tp),
                    "validation": {"ok": True, "trace_sha256": sha, "size_bytes": tp.stat().st_size,
                                   "trace_meta": {"fingerprint": {"model_sha256": "msha", "runtime_sha256": "rsha"}}},
                    "out_dir": str(out_dir), "run_id": f"cap-{workload_id}-{seed}"}
        return fn

    def _patch_chain(self, binding):
        up = {"run_id": "u", "canonical_digest": "d" * 64, "headline": {}}
        return (mock.patch.object(prerequisite, "require_full_chain_pass", return_value=binding),
                mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", binding.reused_trace_sha256),
                mock.patch.object(bridges, "load_upstream_digest", return_value=up))

    def test_prerequisite_chain_required(self):
        with mock.patch.object(prerequisite, "require_full_chain_pass",
                               return_value=prerequisite.R208Binding(prerequisite.STATUS_UPSTREAM_PREREQUISITE_FAILED, "no")):
            res = orchestrator.run(allow_dirty=True)
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PREREQUISITE_NOT_MET)

    def test_coding_v1_seed42_reused_not_recaptured(self):
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        created = {}
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=self._fake_capture(created))
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PASS, res.get("message"))
        self.assertNotIn(("coding-v1", 42), created)
        self.assertEqual(res["headline"]["reused_capture_count"], 1)
        self.assertEqual(res["headline"]["newly_performed_capture_count"], 7)
        self.assertEqual(res["headline"]["canonical_capture_count"], 8)

    def test_all_valid_rerun_zero_captures_and_identical_digest(self):
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        created = {}
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            r1 = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=self._fake_capture(created))
            self.assertEqual(r1["outcome"], orchestrator.OUTCOME_PASS)
            n1 = sum(created.values())
            r2 = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=self._fake_capture(created))
        self.assertEqual(r2["outcome"], orchestrator.OUTCOME_PASS)
        self.assertEqual(sum(created.values()), n1)
        self.assertEqual(r2["canonical_digest"], r1["canonical_digest"])

    def test_h_lt_256_inconclusive(self):
        tr = make_decode_trace(n_decode=200, seed=42, workload="coding-v1")
        path = self.tmp / "reused" / "trace.jsonl.gz"
        sha = write_trace(tr, path)
        binding = self._binding(path, sha)
        created = {}
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf",
                                   capture_fn=self._fake_capture(created, n_decode=200))
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_INCONCLUSIVE)

    def test_failed_capture_never_canonical_and_resumable(self):
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        calls = {"failed": False}
        def flaky(model_path, workload_id, seed, out_dir, **kw):
            if (workload_id, seed) == ("reasoning-v1", 42) and not calls["failed"]:
                calls["failed"] = True
                return {"outcome": "CAPTURE_FAILED", "validation": {"ok": False, "reason": "fail"}}
            return self._fake_capture({})(model_path, workload_id, seed, out_dir, **kw)
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            r1 = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=flaky)
            self.assertEqual(r1["outcome"], orchestrator.OUTCOME_BLOCKED)
            cells = capture_matrix.load_matrix()
            self.assertTrue(cells[("coding-v1", 42)].canonical_status)
            self.assertFalse(cells[("reasoning-v1", 42)].canonical_status)
            r2 = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=self._fake_capture({}))
        self.assertEqual(r2["outcome"], orchestrator.OUTCOME_PASS)

    def test_headline_required_fields_and_claims_safe(self):
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=self._fake_capture({}))
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PASS)
        for k in ["workload_count","canonical_capture_count","reused_capture_count","newly_performed_capture_count",
                  "matched_decode_horizon","temporal_window_length","median_temporal_jsd",
                  "median_same_workload_replicate_jsd","median_cross_workload_jsd",
                  "cross_workload_to_replicate_ratio","cross_workload_to_temporal_ratio",
                  "median_top8_temporal_jaccard_distance","strongest_workload_pair",
                  "most_stable_workload","least_stable_workload","most_stable_layer","least_stable_layer",
                  "regime_classification","all_configuration_fingerprints_compatible"]:
            self.assertIn(k, res["headline"])
        cl = json.loads((Path(res["run_dir"]) / "claims.json").read_text(encoding="utf-8"))
        claims.check_no_forbidden_claims(cl)

    def test_artifacts_and_analyse(self):
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf", capture_fn=self._fake_capture({}))
        rd = Path(res["run_dir"])
        for name in ["summary.json","capture_matrix.json","workload_manifest.json","capture_diagnostics.json",
                     "temporal_drift.csv","replicate_variability.csv","cross_workload.csv","layer_summary.csv",
                     "workload_metrics.json","comparison.json","claims.json","figure_data.json","provenance.json",
                     "temporal_drift.json","replicate_variability.json","cross_workload.json"]:
            self.assertTrue((rd / name).exists(), name)
        a = orchestrator.analyse(run_id=res["run_id"])
        self.assertEqual(a["canonical_digest"], res["canonical_digest"])

    def test_missing_captures_no_model_blocked(self):
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        p1, p2, p3 = self._patch_chain(binding)
        with p1, p2, p3:
            res = orchestrator.run(allow_dirty=True, model_path=None)
        self.assertIn(res["outcome"], (orchestrator.OUTCOME_BLOCKED, orchestrator.OUTCOME_INCONCLUSIVE))

    def test_default_capture_dispatch_runtime_path_passed_once(self):
        """Regression (canonical R2-08 integration): real/default capture dispatch
        must pass runtime_path exactly once. Reproduces the integration path where
        per-cell kwargs already carried runtime_path and the wrapper duplicated it,
        causing TypeError in capture_mod.execute_capture(). Uses the default dispatch
        (capture_fn=None) with execute_capture mocked before any external process."""
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        calls = []
        p1, p2, p3 = self._patch_chain(binding)
        canonical_runtime = r"C:\runtimes\llama\llama-server.exe"
        with p1, p2, p3, \
             mock.patch.object(orchestrator.capture_mod, "execute_capture",
                               side_effect=self._mock_execute_capture(calls)):
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf",
                                   capture_fn=None, runtime_path=canonical_runtime)
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PASS, res.get("message"))
        self.assertEqual(len(calls), 7)  # coding-v1/seed-42 reused
        self.assertNotIn(("coding-v1", 42), {(c["workload_id"], c["seed"]) for c in calls})
        for c in calls:
            self.assertIn("runtime_path", c)
            self.assertEqual(c["runtime_path"], canonical_runtime)
        # Single authoritative source: runtime_path lives only in kwargs (no wrapper duplication).
        self.assertEqual(res["headline"]["newly_performed_capture_count"], 7)

    def test_default_capture_dispatch_runtime_path_single_source_no_conflict(self):
        """Architecture guard: kwargs owns runtime_path for the capture call; the
        default dispatch forwards kwargs without injecting a second runtime_path.
        After this fix the architecture does not permit two independent runtime_path
        sources in the orchestrator dispatch path (conflict would require reintroducing
        wrapper duplication), so there is no silent-choice path to exercise."""
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        calls = []
        p1, p2, p3 = self._patch_chain(binding)
        rt = r"C:\canonical\runtime.exe"
        with p1, p2, p3, \
             mock.patch.object(orchestrator.capture_mod, "execute_capture",
                               side_effect=self._mock_execute_capture(calls)):
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf",
                                   capture_fn=None, runtime_path=rt)
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PASS, res.get("message"))
        self.assertTrue(calls)
        # Exactly the orchestrator run() runtime_path is propagated; no competing source.
        self.assertTrue(all(c["runtime_path"] == rt for c in calls))
        propagated = {c["runtime_path"] for c in calls}
        self.assertEqual(propagated, {rt})

    def test_injectable_capture_fn_still_receives_runtime_path(self):
        """Injectable capture_fn path must keep working with runtime_path in kwargs."""
        rp, rsha = self._make_reused_trace()
        binding = self._binding(rp, rsha)
        seen = []
        created = {}

        def spy_capture(model_path, workload_id, seed, out_dir, allow_dirty=False,
                        runtime_path=None, dry_run=False, **kw):
            seen.append(runtime_path)
            return self._fake_capture(created)(model_path, workload_id, seed, out_dir,
                                               allow_dirty=allow_dirty, runtime_path=runtime_path,
                                               dry_run=dry_run, **kw)

        p1, p2, p3 = self._patch_chain(binding)
        rt = r"C:\rt\llama-server.exe"
        with p1, p2, p3:
            res = orchestrator.run(allow_dirty=True, model_path=r"M:\fake.gguf",
                                   capture_fn=spy_capture, runtime_path=rt)
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PASS, res.get("message"))
        self.assertEqual(len(seen), 7)
        self.assertTrue(all(s == rt for s in seen))


class RunnerTests(TempEvidenceMixin):
    def test_registered_and_roadmap(self):
        self.assertIn("R2-08", runner.EXPERIMENT_RUN_IMPLEMENTATIONS)
        from argine.research2 import roadmap
        rm = roadmap.load(REPO_ROOT / "research2" / "roadmap.json")
        self.assertTrue(rm.is_implemented("R2-08"))
        self.assertEqual(rm.depends_on("R2-08"), ["R2-07"])


if __name__ == "__main__":
    unittest.main()
