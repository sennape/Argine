"""Tests of the G2 evidence seal (no runtime is launched; the committed copies and the exact arithmetic are re-checked)."""
from __future__ import annotations

import copy, importlib.util, json, sys, unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("g2_seal", HERE / "g2_seal.py")
G = importlib.util.module_from_spec(spec); sys.modules["g2_seal"] = G; spec.loader.exec_module(G)  # type: ignore


def rederivation():
    return json.loads((HERE / "REDERIVATION.json").read_text(encoding="utf-8"))


class Seal(unittest.TestCase):
    def test_verify_ok(self):
        self.assertEqual(G.verify(), [])

    def test_exact_H_and_inclusive_threshold(self):
        H = G.exact_h(rederivation()["per_launch"], G.records(G.HOST))
        self.assertEqual(f"{H.numerator}/{H.denominator}", "2072921725103767552/24558530549356953125")
        self.assertGreaterEqual(H, G.THRESHOLD)
        self.assertEqual(G.THRESHOLD, Fraction(1, 25))
        self.assertTrue(Fraction(1, 25) >= G.THRESHOLD)            # exactly 4% passes (inclusive)
        self.assertFalse(Fraction(1, 25) - Fraction(1, 10**30) >= G.THRESHOLD)

    def test_eight_counted_launches_one_warmup_excluded(self):
        recs = G.records(G.HOST)
        counted = [r for r in recs if r.get("type") == "launch" and r.get("counted")]
        self.assertEqual([r["seq"] for r in counted], list(range(1, 9)))
        self.assertEqual([x["seq"] for x in rederivation()["per_launch"]], list(range(1, 9)))
        self.assertEqual(sum(1 for r in recs if r.get("type") == "launch" and not r.get("counted")), 1)

    def test_all_acquisition_checks_hold(self):
        c = G.acquisition_checks(G.HOST)
        self.assertEqual(len(c), 15)
        self.assertTrue(all(c.values()), [k for k, v in c.items() if not v])

    def test_changed_h_ns_fails(self):
        real = rederivation()
        bad = copy.deepcopy(real); bad["per_launch"][3]["h_ns"] += 1
        orig = G.json.loads
        target = (HERE / "REDERIVATION.json").read_text(encoding="utf-8")
        with mock.patch.object(G.json, "loads", side_effect=lambda s, *a, **k: bad if s == target else orig(s, *a, **k)):
            pr = G.verify()
        self.assertTrue(any("exact H" in x or "disagree" in x for x in pr), pr)

    def test_tampered_log_fails(self):
        real = G.lf_sha
        target = (G.HOST / "PHASE1B_LOG.jsonl").resolve()
        with mock.patch.object(G, "lf_sha", side_effect=lambda p: "0" * 64 if Path(p).resolve() == target else real(p)):
            self.assertTrue(any("PHASE1B_LOG" in x for x in G.verify()))

    def test_changed_estimator_after_acquisition_fails(self):
        real = G.lf_sha
        target = (G.P1B / "headroom.py").resolve()
        with mock.patch.object(G, "lf_sha", side_effect=lambda p: "0" * 64 if Path(p).resolve() == target else real(p)):
            self.assertTrue(any("acquisition checks" in x for x in G.verify()))

    def test_a_rerun_in_the_log_is_detected(self):
        real = G.records
        def with_rerun(host):
            r = real(host); return r[:4] + [{"type": "rerun_authorized", "slots": [1]}] + r[4:]
        with mock.patch.object(G, "records", side_effect=with_rerun):
            c = G.acquisition_checks(G.HOST)
        self.assertFalse(c["one_attempt_per_slot_no_reruns"]); self.assertFalse(c["log_order_attempt_received_kept_launch"])

    def test_sanitize(self):
        s = G.sanitize(b'"C:\\\\Users\\\\someone\\\\x" C:\\Users\\someone\\y C:/Users/someone/z', "C:\\Users\\someone")
        self.assertNotIn(b"someone", s); self.assertEqual(s.count(b"%USERPROFILE%"), 3)

    def test_no_personal_path_no_eval_id_no_trace_committed(self):
        ev = [x["id"] for x in json.loads((G.P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]]
        for q in G.sealed_files():
            t = q.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(":\\Users\\", t, q)
            self.assertFalse(any(i in t for i in ev), q)
        self.assertFalse(any(q.suffix in (".gz", ".trace") for q in HERE.rglob("*")))

    def test_interpretation_is_not_a_gain(self):
        s = json.loads((HERE / "G2_SEAL.json").read_text(encoding="utf-8"))
        self.assertIn("NOT an achieved", s["interpretation"])
        self.assertEqual(s["verdict"], "R5_G2_HEADROOM_GATE_PASS")


if __name__ == "__main__":
    unittest.main()
