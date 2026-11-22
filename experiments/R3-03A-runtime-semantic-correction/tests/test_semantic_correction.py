"""R3-03A deterministic, non-model conformance tests.

Builds the REAL production C++ (patches/argine-store.cpp, argine-retention-policy.cpp,
argine-research.cpp) with g++ against a synthetic tiny store and drives the per-node
protocol of argine-experts.cpp (one policy event per (pass, layer); gate/up/down nodes of
the same event are physical-only).  Ground truth is the FROZEN Python reference
(tools/r2_12_real_trace_conformance.py -> AgedFrequency / PredictedProtectPolicy).
No model, no llama-server.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PATCHES = ROOT / "patches"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import r2_12_real_trace_conformance as rtc  # noqa: E402

N_LAYERS, N_EXPERTS, TOP_K = 2, 8, 2
DECODE_PASSES = 80


def _stream(seed: int = 1234):
    """Deterministic routing: pass 0 = prompt (union of 5 experts), then sticky top-2 decode."""
    rng = random.Random(seed)
    lines, cur = [], {l: [0, 1] for l in range(N_LAYERS)}
    for layer in range(N_LAYERS):
        lines.append((0, layer, sorted(rng.sample(range(N_EXPERTS), 5))))
    for p in range(1, DECODE_PASSES + 1):
        for layer in range(N_LAYERS):
            if rng.random() < 0.5:
                cur[layer] = sorted(rng.sample(range(N_EXPERTS), TOP_K))
            lines.append((p, layer, list(cur[layer])))
    return lines


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class SemanticCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="r3_03a_"))
        from make_tiny_model import make_tiny_moe
        from argine import expert_store as es
        from argine.gguf import read_gguf
        gguf = cls.tmp / "tiny.gguf"
        make_tiny_moe(gguf)
        model = read_gguf(str(gguf))
        cls.store = cls.tmp / "tiny.psxs"
        es.write_store(model, es.build_plan(model), cls.store)
        cls.binary = cls.tmp / "semtest"
        cmd = ["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", f"-I{PATCHES}",
               str(PATCHES / "argine-store.cpp"), str(PATCHES / "argine-research.cpp"),
               str(PATCHES / "argine-retention-policy.cpp"), str(PATCHES / "argine-semantic-event-test.cpp"),
               "-o", str(cls.binary), "-lpthread"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-2000:]
        cls.stream = cls.tmp / "stream.txt"
        cls.stream.write_text("".join(f"{p} {l} {' '.join(map(str, e))}\n" for p, l, e in _stream()))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------------
    def _drive(self, policy, decay, interval, budget_experts, legacy=False):
        trace = self.tmp / f"trace_{policy}_{decay}_{interval}_{budget_experts}_{int(legacy)}.jsonl"
        if trace.exists():
            trace.unlink()
        env = dict(os.environ, ARGINE_RETENTION_POLICY=policy, ARGINE_RETENTION_DECAY=str(decay),
                   ARGINE_RETENTION_INTERVAL=str(interval), ARGINE_RETENTION_NEXPERTS=str(N_EXPERTS),
                   ARGINE_RETENTION_TRACE=str(trace))
        if policy.startswith("predicted_protect"):
            env.update(ARGINE_RETENTION_PREDICTOR_DECAY="0.99", ARGINE_RETENTION_TOPK=str(TOP_K))
        args = [str(self.binary), "drive", str(self.store), str(self.stream), str(budget_experts)]
        if legacy:
            args.append("legacy")
        r = subprocess.run(args, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        stats = json.loads(r.stdout.strip().splitlines()[-1])
        rows = [json.loads(l) for l in trace.read_text().splitlines() if l.strip()]
        decisions = [x for x in rows if "complete" not in x]
        return stats, decisions

    # -- tests ----------------------------------------------------------------
    def test_01_cpp_selftest(self):
        r = subprocess.run([str(self.binary), "selftest", str(self.store)], capture_output=True, text=True)
        self.assertIn("RISULTATO OK: 0 fallimenti", r.stdout, r.stdout[-1500:])
        self.assertNotIn("[NO]", r.stdout)

    def test_02_one_policy_event_per_pass_layer(self):
        n_events = len(_stream())
        for policy, decay, interval in (("b_selected", 0.99, 64), ("predicted_protect", 0.99, 64),
                                        ("predicted_protect_sham", 0.99, 64)):
            with self.subTest(policy=policy):
                stats, rows = self._drive(policy, decay, interval, 7)
                self.assertEqual(stats["events"], n_events)
                self.assertEqual(stats["nodes"], 3 * n_events)
                self.assertEqual(stats["events_served"], n_events)
                self.assertEqual(stats["semantic_events_served"], n_events)
                self.assertEqual(stats["followup_nodes_skipped"], 2 * n_events)
                self.assertEqual(stats["duplicate_refused"], 0)
                self.assertEqual(len(rows), n_events)           # rows == semantic events
                # 48-per-token analogue: exactly N_LAYERS policy rows per decode pass
                per_pass = {}
                for r in rows:
                    per_pass[r["semantic_event"]["pass"]] = per_pass.get(r["semantic_event"]["pass"], 0) + 1
                self.assertEqual(set(per_pass.values()), {N_LAYERS})
                self.assertEqual(len(per_pass), DECODE_PASSES + 1)

    def test_03_trace_identity_matches_stream(self):
        stats, rows = self._drive("b_selected", 0.99, 64, 7)
        for r, (p, l, ex) in zip(rows, _stream()):
            se = r["semantic_event"]
            self.assertEqual((se["schema"], se["pass"], se["layer"], se["first_node"]), ("r3-03a-v1", p, l, 1))
            self.assertEqual(se["node_kind"], 0)               # gate opens the event
            self.assertEqual(r["layer"], l)
            self.assertEqual(r["requested_experts"], ex)

    def test_04_no_physical_regressions(self):
        for policy in ("b_selected", "predicted_protect"):
            for budget in (6, 7, 9):
                with self.subTest(policy=policy, budget=budget):
                    stats, _ = self._drive(policy, 0.99, 64, budget)
                    self.assertEqual(stats["bad_followups"], 0)   # no miss/read/corruption at later nodes
                    self.assertEqual(stats["violations"], 0)      # budget never violated
                    # The only source of "logical hit without physical slice" is the pre-existing R2-12 Phase F
                    # margin eviction (physical eviction ahead of the policy for same-event transient victims);
                    # follow-up nodes must introduce no other missing-residency path.
                    self.assertLessEqual(stats["healed"], stats["physical_margin_evictions"])
                    self.assertGreaterEqual(stats["cache_misses"], 3 * stats["policy_misses"])
                    self.assertEqual(stats["duplicate_refused"], 0)

    def test_05_conformance_vs_frozen_r2_reference(self):
        """History, transition counts, decay, protected_before, evictions and (for b_selected)
        score/aging state are all compared decision-by-decision with the frozen Python reference."""
        cases = [("b_selected", 0.99, 64), ("b_selected", 0.999, 16), ("b_selected", 0.999, 1),
                 ("predicted_protect", 0.99, 64), ("predicted_protect", 0.999, 16)]
        for policy, decay, interval in cases:
            for budget in (6, 7, 9):
                with self.subTest(policy=policy, decay=decay, interval=interval, budget=budget):
                    _, rows = self._drive(policy, decay, interval, budget)
                    parsed = rtc.parse_trace([json.dumps(r) for r in rows])
                    ok, mismatch, summary = rtc.compare(parsed)
                    self.assertTrue(ok, f"first mismatch vs frozen reference: {mismatch}")
                    if policy == "predicted_protect":
                        self.assertTrue(any(r["protected_before"] for r in rows))   # not vacuous

    def test_06_sham_matches_b_selected(self):
        for budget in (6, 7):
            _, b = self._drive("b_selected", 0.99, 64, budget)
            _, s = self._drive("predicted_protect_sham", 0.99, 64, budget)
            self.assertEqual(len(b), len(s))
            for rb, rs in zip(b, s):
                for k in ("requested_experts", "hit_experts", "miss_experts", "evicted", "resident_after"):
                    self.assertEqual(rb[k], rs[k], k)
                self.assertEqual(rb["semantic_event"], rs["semantic_event"])

    def test_07_legacy_is_unchanged_and_nonconformant_event_count(self):
        n_events = len(_stream())
        stats, rows = self._drive("predicted_protect", 0.99, 64, 7, legacy=True)
        self.assertEqual(stats["events"], 3 * n_events)           # the OLD 3x behaviour is reproducible
        self.assertEqual(len(rows), 3 * n_events)
        self.assertTrue(all("semantic_event" not in r for r in rows))   # legacy rows byte-compatible
        # legacy protected_before differs from the corrected one (test sensitivity)
        _, new_rows = self._drive("predicted_protect", 0.99, 64, 7)
        legacy_first_of_group = [rows[i] for i in range(0, len(rows), 3)]
        same = sum(1 for a, b in zip(legacy_first_of_group, new_rows) if a["protected_before"] == b["protected_before"])
        self.assertLess(same, len(new_rows))

    def test_08_malformed_duplicate_fails_closed(self):
        # covered at C++ level (selftest B: duplicate (pass, layer) refused, state untouched)
        r = subprocess.run([str(self.binary), "selftest", str(self.store)], capture_output=True, text=True)
        self.assertIn("RIFIUTATO (fail-closed)", r.stdout)
        self.assertIn("NON toccato", r.stdout)


    def test_09_conformance_analyser_accepts_corrected_and_rejects_legacy(self):
        import r3_03a_conformance as cf
        n_pass = DECODE_PASSES + 1
        for policy, sham in (("b_selected", False), ("predicted_protect", False), ("predicted_protect_sham", True)):
            with self.subTest(policy=policy):
                _, rows = self._drive(policy, 0.99, 64, 7)
                an = cf.analyse_trace(rows, n_layers=N_LAYERS, expected_passes=n_pass, sham=sham)
                self.assertTrue(an["ok"], an["failures"])
        _, rows = self._drive("b_selected", 0.99, 64, 7)
        dup = rows + [rows[-1]]
        self.assertFalse(cf.analyse_trace(dup, n_layers=N_LAYERS, expected_passes=n_pass, sham=False)["ok"])
        short = [r for r in rows if not (r["semantic_event"]["pass"] == 5 and r["semantic_event"]["layer"] == 1)]
        self.assertFalse(cf.analyse_trace(short, n_layers=N_LAYERS, expected_passes=n_pass, sham=False)["ok"])
        _, legacy = self._drive("predicted_protect", 0.99, 64, 7, legacy=True)
        an = cf.analyse_trace(legacy, n_layers=N_LAYERS, expected_passes=n_pass, sham=False)
        self.assertFalse(an["ok"])                      # old 3x behaviour is detected by the real-model gate
        over = json.loads(json.dumps(rows))
        over[3]["budget_bytes"] = 1
        self.assertTrue(any("budget" in f or "provenance" in f for f in cf.analyse_trace(over, n_layers=N_LAYERS, expected_passes=n_pass, sham=False)["failures"]))
        bad = json.loads(json.dumps(rows))
        bad[10]["evicted"] = bad[10]["evicted"] + [{"layer": 0, "expert": 99}]
        self.assertFalse(cf.analyse_trace(bad, n_layers=N_LAYERS, expected_passes=n_pass, sham=False)["ok"])


class SourceContractTests(unittest.TestCase):
    """Source-level pins for the runtime wiring (argine-experts.cpp cannot be linked without ggml)."""

    def setUp(self):
        self.ex = (PATCHES / "argine-experts.cpp").read_text(encoding="utf-8")
        self.st = (PATCHES / "argine-store.cpp").read_text(encoding="utf-8")

    def test_experts_uses_event_api_and_skips_policy_for_followups(self):
        self.assertIn("psx_cache_ensure_many_event(c.cache, c.store, totale", self.ex)
        self.assertIn("psx_cache_note_followup_node(c.cache)", self.ex)
        self.assertIn("psx_cache_ensure_event_followup(c.cache", self.ex)
        self.assertIn("c.pol_evt.classify(id.layer, (uint32_t) id.kind)", self.ex)

    def test_followup_nodes_do_not_release_event_pins(self):
        i = self.ex.index("if (!pol_followup) {")
        self.assertIn("psx_cache_unpin(c.cache, p.region, p.offset)", self.ex[i:i + 400])

    def test_identity_is_structural_not_set_based(self):
        h = (PATCHES / "argine-store.h").read_text(encoding="utf-8")
        i = h.index("struct psx_semantic_event_tracker")
        body = h[i:i + 1400]
        self.assertIn("kind_mask", body)
        self.assertNotIn("experts", body.split("Result classify")[1])
        self.assertNotIn("chrono", body)

    def test_policy_is_not_called_on_followup_path(self):
        i = self.ex.index("if (pol_followup) {", self.ex.index("bool fallito = false;"))
        j = self.ex.index("} else if (totale > 0) {", i)
        self.assertNotIn("psx_cache_ensure_many", self.ex[i:j])
        self.assertNotIn("->access", self.ex[i:j])

    def test_duplicate_semantic_event_refused_before_policy_state_change(self):
        a = self.st.index("evento semantico (pass, layer) gia' servito")
        b = self.st.index("c->policy->access(step, layer, dedup_experts")
        self.assertLess(a, b)


if __name__ == "__main__":
    unittest.main()
