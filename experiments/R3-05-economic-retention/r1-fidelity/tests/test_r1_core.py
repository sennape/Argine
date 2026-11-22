"""Synthetic, deterministic tests of the R1 machinery (no real trace needed)."""
import io, json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import r1 as R


def make_trace(n_passes=3, policy="b_selected", budget=4 * 2**30, params=None, tamper=None):
    params = params or {"decay_factor": 0.99, "aging_interval_steps": 64, "predictor_decay": 0, "top_k": 0, "n_experts": 0}
    out, step = [], 0
    for p in range(n_passes):
        for l in range(48):
            req = sorted({(p * 7 + l * 3 + j * 5) % 128 for j in range(8)})
            row = {"step": step, "policy": policy, "layer": l, "bytes_of_layer": 3059712 + l, "requested_experts": req, "hit_experts": [], "miss_experts": req, "budget_bytes": budget,
                   "policy_params": params, "semantic_event": {"schema": "r3-03a-v1", "pass": p, "layer": l, "node_kind": 0, "first_node": 1}}
            if tamper: tamper(row)
            out.append((json.dumps(row) + "\n").encode()); out.append((json.dumps({"step": step, "complete": 1, "cache_resident_bytes_post_service": 1}) + "\n").encode()); step += 1
    return out


class T(unittest.TestCase):
    def test_01_extraction_counts_and_order(self):
        m = R.extract(make_trace(3)); self.assertEqual((m["passes"], m["semantic_events"], m["completion_rows"]), (3, 144, 144))
    def test_02_byte_determinism(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"; m1, m2 = R.extract(make_trace(3), a), R.extract(make_trace(3), b)
            self.assertEqual(a.read_bytes(), b.read_bytes()); self.assertEqual(m1["canonical_stream_sha256"], m2["canonical_stream_sha256"])
            self.assertEqual(R.sha_bytes(a.read_bytes()), m1["canonical_stream_sha256"])
    def test_03_canonical_drops_policy_dependent_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            R.extract(make_trace(2, "b_selected"), a); R.extract(make_trace(2, "predicted_protect_sham", params={"decay_factor": 0.99, "aging_interval_steps": 64, "predictor_decay": 0.99, "top_k": 8, "n_experts": 128}), b)
            self.assertEqual(a.read_bytes(), b.read_bytes())
    def test_04_layer_order_violation_rejected(self):
        t = make_trace(1); rows = [json.loads(x) for x in t]
        rows[2]["layer"] = 5; rows[2]["semantic_event"]["layer"] = 5
        with self.assertRaises(R.R1Error): R.extract([(json.dumps(r) + "\n").encode() for r in rows])
    def test_05_missing_completion_rejected(self):
        with self.assertRaises(R.R1Error): R.extract(make_trace(1)[:-1])
    def test_06_params_change_rejected(self):
        t = make_trace(1); r = json.loads(t[40]); r["budget_bytes"] += 1; t[40] = (json.dumps(r) + "\n").encode()
        with self.assertRaises(R.R1Error): R.extract(t)
    def test_07_first_mismatch_reported(self):
        a = [R.canon_line(i, 0, i, 5, [1, 2]) for i in range(5)]; b = list(a); b[3] = R.canon_line(3, 0, 3, 5, [1, 9])
        mm = R.first_mismatch(a, b); self.assertEqual(mm["event_index"], 3); self.assertIn("[1,9]", mm["b"]); self.assertIsNone(R.first_mismatch(a, a))
        self.assertEqual(R.first_mismatch(a, a[:4])["event_index"], 4)
    def test_08_source_identity_rejections(self):
        frozen = {"trace_sha256": "x" * 64, "trace_bytes": 10, "policy_id": "b_selected", "budget_bytes": 4 * 2**30, "token_digest": R.TOKEN_DIGEST}
        meta = {"raw_sha256": "x" * 64, "policy_id": "b_selected", "budget_bytes": 4 * 2**30, "passes": R.N_PASSES, "semantic_events": R.N_EVENTS}
        self.assertEqual(R.check_source_identity(meta, frozen, 10), [])
        self.assertTrue(R.check_source_identity(dict(meta, raw_sha256="y" * 64), frozen, 10))           # wrong source
        self.assertTrue(R.check_source_identity(meta, dict(frozen, token_digest="0" * 64), 10))         # wrong digest
        self.assertTrue(R.check_source_identity(dict(meta, budget_bytes=2**33), frozen, 10))           # budget mismatch
        self.assertTrue(R.check_source_identity(dict(meta, passes=513), frozen, 10))
        self.assertTrue(R.check_source_identity(meta, frozen, 11))
    def test_09_replay_A_matches_independent_simulator(self):
        reqs = [[1, 2], [1, 3], [4, 5], [1, 2], [3, 4], [1, 5], [2, 3], [1, 4]] * 3
        lines = [R.canon_line(i, 0, 0, 1, r) for i, r in enumerate(reqs)]
        rep = R.replay(lines, "A", 4, {"decay_factor": 0.999, "aging_interval_steps": 1, "predictor_decay": 0, "top_k": 0, "n_experts": 0})
        # independent aged-frequency simulator (event-atomic, ascending members, victim = min (score,last_ref,key))
        res, sc, lr, t, miss, hit, ev = set(), {}, {}, 0, 0, 0, 0
        for r in reqs:
            for e in sorted(set(r)):
                t += 1
                for k in list(res): sc[k] *= 0.999
                if e in res: hit += 1; sc[e] += 1; lr[e] = t; continue
                miss += 1
                while len(res) + 1 > 4 and res:
                    v = min(res, key=lambda k: (sc[k], lr[k], (0, k))); res.discard(v); ev += 1
                res.add(e); sc[e] = 1.0; lr[e] = t
        a = rep["all_pass"]; self.assertEqual((a["misses"], a["hits"], a["evictions"]), (miss, hit, ev))
    def test_10_gate2_comparison_exact_and_detects_one_off(self):
        rep = {"all_pass": {"misses": 10, "hits": 5, "evictions": 8, "miss_bytes": 100}, "decode": {"misses": 512, "hits": 1024, "evictions": 512, "miss_bytes": 1024, "protected_events": 0, "protected_entries": 0}, "peak_policy_resident_bytes": 7}
        exp = {"all_pass_misses": 10, "all_pass_hits": 5, "all_pass_evictions": 8, "all_pass_miss_bytes": 100, "decode_misses_per_token": 1.0, "decode_hits_per_token": 2.0, "decode_evictions_per_token": 1.0,
               "decode_miss_bytes_per_token": 2.0, "decode_protected_events": 0, "decode_protected_entries": 0, "resident_peak_bytes": 7}
        self.assertTrue(all(r["exact"] for r in R.compare_gate2(rep, exp)))
        rep["decode"]["misses"] = 513; self.assertFalse(all(r["exact"] for r in R.compare_gate2(rep, exp)))
    def test_11_frozen_sources_read_from_frozen_evidence(self):
        f = R.frozen_sources(); self.assertEqual({k: v["token_digest"] for k, v in f.items()}, {k: R.TOKEN_DIGEST for k in "ABC"})
        self.assertEqual(f["A"]["trace_sha256"], "154502dafdefede307f76169d830a268dc67c86ff1945e9f8fd0e6d38755b990")

if __name__ == "__main__": unittest.main()
