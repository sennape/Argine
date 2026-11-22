"""R3-03B instrumentation tests: observational only (no model, no timing claims).

The production C++ (argine-store.cpp + argine-retention-policy.cpp) is built with g++ against the tiny store
used by R3-03A; ARGINE_POLICY_TIMING=1 must change NOTHING except the additive timing counters.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests"),
                str(ROOT / "experiments" / "R3-03A-runtime-semantic-correction" / "tests")]
import test_semantic_correction as base  # noqa: E402

POLICIES = (("b_selected", 0.99, 64), ("predicted_protect", 0.99, 64), ("predicted_protect_sham", 0.99, 64))


class InstrumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.SemanticCorrectionTests.setUpClass()
        b = base.SemanticCorrectionTests
        cls.tmp, cls.store, cls.binary, cls.stream = b.tmp, b.store, b.binary, b.stream

    @classmethod
    def tearDownClass(cls):
        base.SemanticCorrectionTests.tearDownClass()

    def _run(self, policy, decay, interval, budget, timing):
        tag = f"{policy}_{budget}_{int(timing)}"
        trace = self.tmp / f"tm_{tag}.jsonl"
        if trace.exists():
            trace.unlink()
        env = dict(os.environ, ARGINE_RETENTION_POLICY=policy, ARGINE_RETENTION_DECAY=str(decay),
                   ARGINE_RETENTION_INTERVAL=str(interval), ARGINE_RETENTION_NEXPERTS=str(base.N_EXPERTS),
                   ARGINE_RETENTION_TRACE=str(trace))
        env.pop("ARGINE_POLICY_TIMING", None)
        if timing:
            env["ARGINE_POLICY_TIMING"] = "1"
        if policy.startswith("predicted_protect"):
            env.update(ARGINE_RETENTION_PREDICTOR_DECAY="0.99", ARGINE_RETENTION_TOPK=str(base.TOP_K))
        r = subprocess.run([str(self.binary), "drive", str(self.store), str(self.stream), str(budget)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        return json.loads(r.stdout.strip().splitlines()[-1]), trace.read_bytes()

    def test_01_disabled_is_inert(self):
        for p, d, i in POLICIES:
            st, _ = self._run(p, d, i, 7, False)
            self.assertEqual(st["timing_enabled"], 0)
            for k in ("t_calls", "t_total", "t_pred", "t_victim", "t_victim_calls", "t_lock_wait", "t_locked", "t_hist_sum"):
                self.assertEqual(st[k], 0, k)

    def test_02_enabling_changes_no_decision(self):
        for p, d, i in POLICIES:
            for budget in (6, 7, 9):
                with self.subTest(policy=p, budget=budget):
                    st0, tr0 = self._run(p, d, i, budget, False)
                    st1, tr1 = self._run(p, d, i, budget, True)
                    self.assertEqual(tr0, tr1)                       # byte-identical decision trace
                    for k, v in st0.items():
                        if not k.startswith("t_") and k != "timing_enabled":
                            self.assertEqual(st1[k], v, k)           # every non-timing counter identical
                    self.assertEqual(st1["timing_enabled"], 1)

    def test_03_counters_are_consistent(self):
        for p, d, i in POLICIES:
            with self.subTest(policy=p):
                st, tr = self._run(p, d, i, 7, True)
                rows = [json.loads(l) for l in tr.decode().splitlines() if l.strip() and '"complete"' not in l]
                self.assertEqual(st["t_calls"], len(rows))
                self.assertEqual(st["t_calls"], st["semantic_events_served"])
                self.assertEqual(st["t_hist_sum"], st["t_calls"])
                self.assertEqual(st["t_victim_calls"], st["policy_evictions"])
                self.assertEqual(st["t_victim_calls"], sum(len(r["evicted"]) for r in rows))
                self.assertGreater(st["t_total"], 0)
                self.assertLessEqual(st["t_pred"], st["t_total"])
                self.assertLessEqual(st["t_victim"], st["t_total"])
                self.assertLessEqual(st["t_pred"] + st["t_victim"], st["t_total"])
                self.assertGreaterEqual(st["t_locked"], st["t_total"])   # shard lock is held across the policy call
                self.assertGreaterEqual(st["t_lock_wait"], 0)

    def test_04_source_contract(self):
        pol = (ROOT / "patches" / "argine-retention-policy.cpp").read_text()
        sto = (ROOT / "patches" / "argine-store.cpp").read_text()
        self.assertIn('getenv("ARGINE_POLICY_TIMING")', sto)
        self.assertRegex(sto, r"tm_env\[0\] == '1' && tm_env\[1\] == '\\0'")        # exact "1" only
        self.assertIn("const bool tm = timing_on_;", pol)
        self.assertEqual(len(re.findall(r"tm \? policy_now_ns\(\) : 0", pol)), 2)    # both clock reads are gated
        self.assertNotIn("timing_", re.sub(r"if \(tm\) \{.*?\n    \} else \{", "", pol, flags=re.S).split("RetentionStats RetentionPolicy::stats")[0].split("EventOutcome RetentionPolicy::access")[0])


if __name__ == "__main__":
    unittest.main()
