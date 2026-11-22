"""R3-02E: free-RAM feasibility gate diagnosis tests (offline, no model process)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import r3_02_acquire as A  # noqa: E402
from argine import hardware as hwmod, planner as planmod  # noqa: E402
from argine.gguf import find_gguf, read_gguf  # noqa: E402

GiB = 1024 ** 3
BUDGET = 8 * GiB
MODEL = Path(r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")


@unittest.skipUnless(MODEL.exists(), "model metadata not present")
class TestExactPlannerRequest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = read_gguf(find_gguf(str(MODEL)))

    def _plan(self, available):
        hw = hwmod.detect(extra_disk_paths=[str(MODEL.parent)])
        hw.ram_available = int(available)
        req = planmod.PlanRequest(ctx=4096, threads=0, backend="cpu",
                                  expert_store=True, expert_store_budget=BUDGET)
        return hw, planmod.plan(self.model, hw, req)

    def test_budget_is_available_minus_512_mib(self):
        hw, p = self._plan(10.19 * GiB)
        self.assertLess(hw.ram_available, hw.ram_total * 0.75)
        self.assertEqual(p.budget, int(10.19 * GiB) - 512 * 1024 ** 2)

    def test_expert_budget_not_double_counted(self):
        _, p = self._plan(12 * GiB)
        self.assertEqual(p.estimate.expert_cache, BUDGET)
        self.assertEqual(p.expert_cache_budget, BUDGET)

    def test_weights_use_shared_bytes_not_whole_model(self):
        _, p = self._plan(12 * GiB)
        self.assertEqual(p.estimate.weights_resident, self.model.shared_bytes)
        self.assertLess(p.estimate.weights_resident, self.model.tensor_bytes)

    def test_estimate_total_is_component_sum(self):
        _, p = self._plan(12 * GiB)
        e = p.estimate
        self.assertEqual(e.total, e.weights_resident + e.expert_cache + e.repack +
                         e.kv_cache + e.prompt_cache + e.activations + e.runtime + e.draft)

    def test_infeasible_at_10_19_gib(self):
        _, p = self._plan(10.19 * GiB)
        self.assertFalse(p.feasible)
        self.assertGreater(p.estimate.total, p.budget)
        self.assertAlmostEqual((p.estimate.total - p.budget) / GiB, 0.352, places=2)

    def test_feasible_at_10_60_gib(self):
        _, p = self._plan(10.60 * GiB)
        self.assertTrue(p.feasible)
        self.assertGreaterEqual(p.budget, p.estimate.total)

    def test_fail_closed_below_requirement(self):
        _, p = self._plan(9.0 * GiB)
        self.assertFalse(p.feasible)

    def test_available_ram_source_is_live(self):
        import psutil
        hw = hwmod.detect()
        live = psutil.virtual_memory().available
        self.assertLessEqual(abs(hw.ram_available - live), 512 * 1024 ** 2)

    def test_driver_gate_still_fails_closed(self):
        original = A._load_gate_inputs
        A._load_gate_inputs = lambda p: (self.model, self._plan(10.19 * GiB)[0])
        try:
            with self.assertRaises(A.LaunchFailure):
                A.default_free_ram_check(BUDGET)
        finally:
            A._load_gate_inputs = original

    def test_completed_a8_evidence_not_modified(self):
        run = Path(r"C:\Users\alpel\Desktop\Argine4-r3runs\R3-02-p4")
        rec = json.loads((run / "records" / "trace_count-A-8GiB.json").read_text(encoding="utf-8"))
        adm = json.loads((run / "admissions" / "trace_count-A-8GiB.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["telemetry"]["counters"]["decision_rows"], 74016)
        self.assertEqual(rec["telemetry"]["counters"]["resident_bytes_peak"], 8589901824)
        self.assertEqual(rec["telemetry"]["trace_sha256"],
                         "a598543f1e6df6f8767361c6d34fae23879e28d661077edbde612f33e8495901")
        self.assertEqual(adm["status"], "ADMISSIBLE")
        self.assertFalse((run / "traces" / "A-8GiB.jsonl").exists())

    def test_no_process_started(self):
        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            self._plan(10.6 * GiB)
        finally:
            A.subprocess.Popen = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
