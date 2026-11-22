"""Unit tests of the Phase 1a host-conformance driver (no runtime is launched)."""
from __future__ import annotations

import copy, importlib.util, json, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host_conformance", HERE / "host_conformance.py")
H = importlib.util.module_from_spec(spec); sys.modules["host_conformance"] = H; spec.loader.exec_module(H)  # type: ignore
EVAL = {p["id"] for p in json.loads((H.P0 / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]}


def good_records():
    recs = []
    for s in H.plan():
        r = dict(s)
        r.update({"error": None, "n_tokens": 512, "token_digest": "d-" + s["prompt"], "shutdown": {"mode": "graceful"},
                  "server_timings": {"predicted_ms": 512 * 250.0, "predicted_n": 512}})
        if s["runtime"] == "r5":
            arm = s["arm"]
            r["identity"] = {"expert_wait": H.ARMS[arm], "how": "esplicita", "prefetch": "0"}
            r["r5_counters_lines"] = 1
            r["r5_counters"] = {"misses": 1000, "bytes_read": 10 ** 9, "evictions": 900, "violations": 0, "orphan_pending": 0,
                                "pending_at_event_end": 0, "touches": 0 if arm == "A" else 5000, "deferred_reads": 600 if arm == "C" else 0,
                                "issued_followup": 0, "first_nodes": 0 if arm == "A" else 24576, "followup_nodes": 0 if arm == "A" else 49152,
                                "followup_mismatch": 0, "touch_missing": 0, "drained_at_thread_exit": 0}
        if s["traced"]:
            r["trace"] = {"present": True, "structure": {"checks": {"x": True, "y": True}}}
        recs.append(r)
    return recs


MF = {"returncode": 0, "passed": 136, "failed": 0, "skipped": 3}
SM = {"version_returncode": 0, "refusals": [{"pass": True}] * 3}


class Plan(unittest.TestCase):
    def test_counts_and_development_only(self):
        p = H.plan()
        self.assertEqual(len(p), 42)
        self.assertEqual(sum(1 for s in p if s["step"] == "arms"), 24)
        self.assertEqual(sum(1 for s in p if s["step"] == "fidelity"), 16)
        self.assertEqual(sum(1 for s in p if s["step"] == "instrumentation"), 2)
        self.assertFalse({s["prompt"] for s in p} & EVAL)
        self.assertEqual({s["prompt"] for s in p}, {"code-dev-1", "nl-dev-1", "math-dev-1", "json-dev-1"})

    def test_dev_prompts_match_frozen_hashes(self):
        ids = [x["id"] for x in H.dev_prompts()]
        self.assertEqual(sorted(ids), sorted(["code-dev-1", "nl-dev-1", "math-dev-1", "json-dev-1"]))

    def test_environment_per_arm(self):
        base = {"ARGINE_RETENTION_POLICY": "b_selected", "ARGINE_EXPERT_PRETOUCH": "1", "ARGINE_PREFETCH": "3"}
        for arm, mode in H.ARMS.items():
            e = H.build_env({"budget": "1.5", "runtime": "r5", "arm": arm}, Path("s.psxs"), None, base)
            self.assertEqual(e["ARGINE_EXPERT_WAIT"], mode)
            self.assertEqual((e["ARGINE_PREFETCH"], e["ARGINE_EXPERT_READ"], e["ARGINE_EXPERT_BUDGET"]), ("0", "asincrono", "1610612736"))
            self.assertNotIn("ARGINE_RETENTION_POLICY", e); self.assertNotIn("ARGINE_EXPERT_PRETOUCH", e); self.assertNotIn("ARGINE_RESEARCH_TRACE", e)
        v5 = H.build_env({"budget": "4", "runtime": "v5", "arm": "A"}, Path("s.psxs"), None, base)
        self.assertNotIn("ARGINE_EXPERT_WAIT", v5)
        tr = H.build_env({"budget": "1.5", "runtime": "r5", "arm": "C"}, Path("s.psxs"), Path("t.trace"), {})
        self.assertEqual(tr["ARGINE_RESEARCH_TRACE"], "t.trace")

    def test_stderr_parsing(self):
        txt = 'x\nargine: attesa expert: kind (esplicita), ARGINE_PREFETCH=0, schema r5-ets-v1\nargine: r5_counters {"misses": 3, "violations": 0}\n'
        p = H.parse_stderr(txt)
        self.assertEqual(p["identity"], {"expert_wait": "kind", "how": "esplicita", "prefetch": "0"})
        self.assertEqual(p["r5_counters"]["misses"], 3); self.assertEqual(p["r5_counters_lines"], 1)


class Evaluate(unittest.TestCase):
    def ev(self, recs):
        return H.evaluate(recs, MF, SM)

    def test_pass(self):
        r = self.ev(good_records())
        self.assertEqual(r["verdict"], "R5_G1_HOST_CONFORMANCE_PASS", r["criteria"])

    def test_no_arm_timing_contrast_is_ever_reported(self):
        r = self.ev(good_records())
        self.assertEqual(set(r["trace_overhead_same_arm"]), {"B_vs_A", "C_I_vs_C"})
        flat = json.dumps(r)
        for bad in ("C_vs_A", "C0_vs_A", "speedup", "H_ms", "exposed_wait"):
            self.assertNotIn(bad, flat)

    def _mut(self, f):
        recs = good_records(); f(recs); return self.ev(recs)

    def test_token_mismatch_fails(self):
        def f(rs): next(r for r in rs if r["arm"] == "C")["token_digest"] = "other"
        self.assertFalse(self._mut(f)["criteria"]["identical_tokens_all_arms_budgets_and_v5"])

    def test_v5_token_mismatch_fails(self):
        def f(rs): next(r for r in rs if r["runtime"] == "v5")["token_digest"] = "other"
        self.assertFalse(self._mut(f)["criteria"]["identical_tokens_all_arms_budgets_and_v5"])

    def test_violation_and_pending_fail(self):
        def f(rs): next(r for r in rs if r["arm"] == "C")["r5_counters"]["violations"] = 1
        self.assertFalse(self._mut(f)["criteria"]["zero_budget_violations"])
        def g(rs): next(r for r in rs if r["arm"] == "C")["r5_counters"]["pending_at_event_end"] = 2
        self.assertFalse(self._mut(g)["criteria"]["zero_pending_reads_at_event_boundaries"])

    def test_purity(self):
        def ok(rs): next(r for r in rs if r["arm"] == "C" and r["step"] == "arms")["r5_counters"]["misses"] = 1010   # exactly 1%
        self.assertTrue(self._mut(ok)["criteria"]["scheduling_purity_1pct"])
        def bad(rs): next(r for r in rs if r["arm"] == "C0" and r["step"] == "arms")["r5_counters"]["evictions"] = 910  # 1.1%
        self.assertFalse(self._mut(bad)["criteria"]["scheduling_purity_1pct"])

    def test_bookkeeping_control_and_deferral(self):
        def f(rs): next(r for r in rs if r["arm"] == "C0")["r5_counters"]["deferred_reads"] = 1
        self.assertFalse(self._mut(f)["criteria"]["bookkeeping_control_exercised"])
        def g(rs): next(r for r in rs if r["arm"] == "C")["r5_counters"]["deferred_reads"] = 0
        self.assertFalse(self._mut(g)["criteria"]["kind_arm_defers"])

    def test_fidelity(self):
        def f(rs):
            for r in rs:
                if r["step"] == "fidelity" and r["runtime"] == "r5":
                    r["server_timings"]["predicted_ms"] *= 1.025
        res = self._mut(f)
        self.assertFalse(res["criteria"]["baseline_fidelity_within_2pct"])
        self.assertAlmostEqual(res["baseline_fidelity"]["relative_slowdown"], 0.025)

    def test_identity_and_shutdown_and_missing(self):
        def f(rs): next(r for r in rs if r["arm"] == "C")["identity"]["prefetch"] = "2"
        self.assertFalse(self._mut(f)["criteria"]["arm_identity_echoed"])
        def g(rs): rs[0]["shutdown"]["mode"] = "terminate_fallback"
        self.assertFalse(self._mut(g)["criteria"]["graceful_shutdown"])
        def h(rs): rs[5]["error"] = "boom"
        self.assertFalse(self._mut(h)["criteria"]["all_launches_complete"])
        def i(rs): next(r for r in rs if r["traced"])["trace"]["structure"]["checks"]["x"] = False
        self.assertFalse(self._mut(i)["criteria"]["instrumentation_complete"])

    def test_bookkeeping_must_match_between_C0_and_C(self):
        def f(rs): next(r for r in rs if r["arm"] == "C0" and r["step"] == "arms")["r5_counters"]["touches"] = 4999
        self.assertFalse(self._mut(f)["criteria"]["bookkeeping_identical_C0_C"])

    def test_fail_closed_and_drain_counters(self):
        for k in ("followup_mismatch", "touch_missing", "drained_at_thread_exit"):
            def f(rs, k=k): next(r for r in rs if r["arm"] == "C")["r5_counters"][k] = 1
            self.assertFalse(self._mut(f)["criteria"]["zero_fail_closed_and_drain_events"], k)

    def test_windows_counts_are_pinned(self):
        for mf in ({"returncode": 0, "passed": 136, "failed": 0, "skipped": 4}, {"returncode": 0, "passed": 135, "failed": 0, "skipped": 3}):
            self.assertFalse(H.evaluate(good_records(), mf, SM)["criteria"]["windows_model_free_tests"])

    def test_model_free_and_smoke_required(self):
        self.assertFalse(H.evaluate(good_records(), {"returncode": 1, "failed": 2, "passed": 3}, SM)["criteria"]["windows_model_free_tests"])
        self.assertFalse(H.evaluate(good_records(), MF, {"version_returncode": 0, "refusals": [{"pass": True}, {"pass": False}, {"pass": True}]})["criteria"]["startup_smoke_and_fail_closed"])


if __name__ == "__main__":
    unittest.main()
