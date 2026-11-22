"""R3-02H multi-session plan tests: deterministic, offline, no model."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PHASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import r3_02g_session as S  # noqa: E402

SCHED = S.build_schedule()
PLAN = json.loads((PHASE / "SESSION_PLAN.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((PHASE / "EXECUTION_CONTRACT.json").read_text(encoding="utf-8"))
FROZEN = json.loads((PHASE / "GLOBAL_SCHEDULE.json").read_text(encoding="utf-8"))


class TestSchedule(unittest.TestCase):
    def test_six_hundred_unique_pairs(self):
        self.assertEqual(len(SCHED["pairs"]), 600)
        self.assertEqual([p["pair"] for p in SCHED["pairs"]], list(range(1, 601)))

    def test_twelve_hundred_unique_launches(self):
        self.assertEqual(len(SCHED["launches"]), 1200)
        ids = {(l["pair"], l["slot"]) for l in SCHED["launches"]}
        self.assertEqual(len(ids), 1200)

    def test_pairs_per_session(self):
        for s in range(1, 11):
            self.assertEqual(len([p for p in SCHED["pairs"] if p["session"] == s]), 60)

    def test_launches_per_session(self):
        for s in range(1, 11):
            self.assertEqual(len(S.session_launches(SCHED, s)), 120)

    def test_no_pair_crosses_session(self):
        for p in SCHED["pairs"]:
            self.assertEqual(len({l["session"] for l in SCHED["launches"] if l["pair"] == p["pair"]}), 1)

    def test_ab_ba_balance(self):
        ab = sum(1 for p in SCHED["pairs"] if p["orientation"] == "AB")
        ba = sum(1 for p in SCHED["pairs"] if p["orientation"] == "BA")
        self.assertEqual((ab, ba), (300, 300))

    def test_block_alternation(self):
        for block in range(1, 301):
            ps = [p["orientation"] for p in SCHED["pairs"] if p["block"] == block]
            expect = ["AB", "BA"] if block % 2 == 1 else ["BA", "AB"]
            self.assertEqual(ps, expect)

    def test_deterministic_digest(self):
        self.assertEqual(S.schedule_digest(S.build_schedule()), S.schedule_digest(SCHED))
        self.assertEqual(S.schedule_digest(SCHED), PLAN["schedule_digest"])
        self.assertEqual(S.schedule_digest(SCHED), FROZEN["digest"])

    def test_validate_clean(self):
        self.assertEqual(S.validate_schedule(SCHED), [])


class TestResume(unittest.TestCase):
    def _state(self, tmp):
        return S.SessionState(Path(tmp) / "state.json")

    def test_completed_launch_never_reruns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            st = self._state(tmp)
            for l in S.session_launches(SCHED, 1):
                st.mark(l["global_launch"], "SUCCEEDED")
            self.assertEqual(S.pairs_complete(SCHED, st, 1), 60)
            self.assertEqual(S.launches_complete(st, 1, SCHED), 120)

    def test_resume_after_reboot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            st = self._state(tmp)
            st.mark(1, "SUCCEEDED")
            reloaded = S.SessionState(Path(tmp) / "state.json")
            self.assertEqual(reloaded.status(1), "SUCCEEDED")

    def test_interrupted_pair_recovery(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            st = self._state(tmp)
            pair1 = SCHED["pairs"][0]
            st.mark(pair1["launches"][0], "SUCCEEDED")
            st.mark(pair1["launches"][1], "FAILED")
            self.assertEqual(S.pairs_complete(SCHED, st, 1), 0)
            st.mark(pair1["launches"][1], "SUCCEEDED")
            self.assertEqual(S.pairs_complete(SCHED, st, 1), 1)

    def test_session_range_isolation(self):
        for s in range(1, 11):
            ls = S.session_launches(SCHED, s)
            self.assertTrue(all(l["session"] == s for l in ls))
            self.assertEqual(min(l["pair"] for l in ls), (s - 1) * 60 + 1)
            self.assertEqual(max(l["pair"] for l in ls), s * 60)


class TestAnalysisLock(unittest.TestCase):
    def test_locked_before_600(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = S.SessionState(d / "state.json")
            self.assertEqual(S.analysis_status(st, SCHED, d)["status"], "R3_02G_EVAL_LOCKED")

    def test_unlocks_only_at_full_completion(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = S.SessionState(d / "state.json")
            for l in SCHED["launches"]:
                st.mark(l["global_launch"], "SUCCEEDED")
            (d / "admissions").mkdir()
            for i in range(1200):
                (d / "admissions" / f"timing-{i}.json").write_text("{}")
            self.assertEqual(S.analysis_status(st, SCHED, d)["status"], "R3_02G_EVAL_READY")

    def test_monitor_has_no_scientific_aggregate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = S.SessionState(d / "state.json")
            info = S.monitor(st, SCHED, d, 1)
            forbidden = {"median", "ci", "precision", "overhead", "effect", "bootstrap"}
            self.assertFalse(forbidden & {k.lower() for k in info})
            self.assertIn("global_pairs_complete", info)

    def test_no_optional_stopping(self):
        self.assertFalse(CONTRACT["analysis_lock"].get("optional_stopping", False))
        self.assertEqual(CONTRACT["failure_semantics"]["n_fixed"], 600)
        self.assertIn("no precision verdict", CONTRACT["no_interim_analysis"])


class TestFrozenEvidence(unittest.TestCase):
    def test_r3_02_and_g_frozen_unchanged(self):
        g = json.loads((REPO / "experiments/R3-02G-precision-extension/FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(g["chosen_N"], 600)
        p2 = json.loads((REPO / "experiments/R3-02-phase2-mechanism/FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(p2["status"], "MECHANISM_INSUFFICIENT")

    def test_no_process_started(self):
        import subprocess as sp
        original = sp.Popen
        sp.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            S.validate_schedule(SCHED)
        finally:
            sp.Popen = original




class TestExecutionAdapter(unittest.TestCase):
    """R3-02H hotfix: full R3-02H -> R3-02B execution-launch adaptation."""

    PLAN = json.loads((REPO / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text(encoding="utf-8"))

    def test_all_1200_counted_adapt(self):
        adapted = [S.build_execution_launch(e, self.PLAN) for e in SCHED["launches"]]
        self.assertEqual(len(adapted), 1200)

    def test_kind_is_timing(self):
        for e in SCHED["launches"][:50]:
            self.assertEqual(S.build_execution_launch(e, self.PLAN)["kind"], "TIMING")

    def test_policy_a_maps(self):
        e = next(x for x in SCHED["launches"] if x["policy"] == "A")
        a = S.build_execution_launch(e, self.PLAN)
        self.assertEqual(a["config_id"], "A-4GiB")
        self.assertEqual(a["kind"], "TIMING")

    def test_policy_b_maps(self):
        e = next(x for x in SCHED["launches"] if x["policy"] == "B")
        b = S.build_execution_launch(e, self.PLAN)
        self.assertEqual(b["config_id"], "B-4GiB")

    def test_budget_validation(self):
        bad = json.loads(json.dumps(self.PLAN))
        for c in bad["configurations"]:
            c["budget_bytes"] = 8 * 1024 ** 3
        with self.assertRaises(ValueError):
            S.build_execution_launch(SCHED["launches"][0], bad)

    def test_identity_preserved(self):
        for e in SCHED["launches"][:100]:
            a = S.build_execution_launch(e, self.PLAN)
            self.assertEqual(a["pair"], e["pair"])
            self.assertEqual(a["session"], e["session"])
            self.assertEqual(a["session_pair"], e["session_pair"])
            self.assertEqual(a["global_launch"], e["global_launch"])
            self.assertEqual(a["seq"], e["global_launch"])

    def test_adapter_does_not_mutate_entry(self):
        e = dict(SCHED["launches"][0])
        before = json.dumps(e, sort_keys=True)
        S.build_execution_launch(e, self.PLAN)
        self.assertEqual(json.dumps(e, sort_keys=True), before)

    def test_fail_closed(self):
        good = SCHED["launches"][0]
        cases = []
        bad = dict(good); bad["policy"] = "C"; cases.append(bad)
        bad = dict(good); bad["config_id"] = "B-4GiB"; cases.append(bad)
        bad = dict(good); bad["session"] = 2; cases.append(bad)
        bad = dict(good); bad["global_launch"] = 1201; cases.append(bad)
        bad = dict(good); bad["slot"] = 3 - good["slot"]; cases.append(bad)
        bad = dict(good); bad["counted"] = False; cases.append(bad)
        for case in cases:
            with self.assertRaises(ValueError):
                S.build_execution_launch(case, self.PLAN)

    def test_downstream_fields_present(self):
        a = S.build_execution_launch(SCHED["launches"][0], self.PLAN)
        for key in ("seq", "kind", "config_id", "n_predict", "repetition", "counted", "warmup"):
            self.assertIn(key, a)

    def test_full_default_run_one_traversal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            plan = self.PLAN

            class Runner:
                def run_launch(self, launch, *, trace_path=None):
                    d.joinpath("logs").mkdir(parents=True, exist_ok=True)
                    e = d / "logs" / "e.log"; e.write_text("ok")
                    o = d / "logs" / "o.log"; o.write_text("ok")
                    return {"tokens": [1, 2, 3], "timings": {"prompt_ms": 500, "predicted_ms": 1000},
                            "wall_seconds": 1.0, "prompt_seconds": 0.5, "predicted_seconds": 1.0,
                            "exit_code": 0, "stdout_path": str(o), "stderr_path": str(e),
                            "process_cpu_seconds": 0.25, "request_completed_successfully": True,
                            "shutdown": {"process_alive_before_shutdown": True, "terminate_used": True,
                                         "kill_used": False, "exit_code": 0}}

            launch = S.build_execution_launch(SCHED["launches"][0], plan)
            result = S.A._default_run_one(launch, plan, d, Runner())
            self.assertTrue(S.A.launch_clean(result["evidence"]))
            self.assertEqual(result["record"]["mode"], "TIMING")

    def test_two_warmups(self):
        adapted = S.execution_launches(SCHED, self.PLAN, 1)
        warm = [a for a in adapted if a["warmup"]]
        self.assertEqual([w["policy"] for w in warm], ["A", "B"])
        self.assertTrue(all(w["kind"] == "TIMING" and w["counted"] is False for w in warm))

    def test_session_execution_launch_counts(self):
        self.assertEqual(len(S.execution_launches(SCHED, self.PLAN, 1)), 122)
        self.assertEqual(len(S.execution_launches(SCHED, self.PLAN, 2)), 120)

    def test_schedule_digest_unchanged(self):
        self.assertEqual(S.schedule_digest(SCHED), "ba7eb1b7e94f89ed1d5d02c4a9742f8307bc050860cc08fdd23aeae0832b5e40")

    def test_frozen_artifacts_unchanged(self):
        h = hashlib.sha256((PHASE / "GLOBAL_SCHEDULE.json").read_bytes()).hexdigest()
        freeze = json.loads((PHASE / "FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(freeze["artifacts"]["GLOBAL_SCHEDULE.json"], h)

    def test_no_process_started(self):
        import subprocess as sp
        original = sp.Popen
        sp.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            S.execution_launches(SCHED, self.PLAN, 1)
        finally:
            sp.Popen = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
