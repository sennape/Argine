"""Tests of the G1 evidence seal (no runtime is launched; the committed copies are re-evaluated)."""
from __future__ import annotations

import importlib.util, json, sys, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("g1_seal", HERE / "g1_seal.py")
G = importlib.util.module_from_spec(spec); sys.modules["g1_seal"] = G; spec.loader.exec_module(G)  # type: ignore


class Seal(unittest.TestCase):
    def test_verify_ok(self):
        self.assertEqual(G.verify(), [])

    def test_recomputed_verdict(self):
        res = G.recompute_verdict(G.HOST)
        self.assertEqual(res["verdict"], "R5_G1_HOST_CONFORMANCE_PASS")
        self.assertEqual(len(res["criteria"]), 17)
        self.assertTrue(all(res["criteria"].values()))
        self.assertEqual(res["missing_or_failed_slots"], [])

    def test_forty_two_launches_once_each(self):
        recs = [json.loads(l) for l in (G.HOST / "DEV_LOG.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(sorted(r["seq"] for r in recs), list(range(1, 43)))
        self.assertTrue(all(r["error"] is None and r["n_tokens"] == 512 for r in recs))

    def test_tampered_record_fails(self):
        real = G.lf_sha
        target = (G.HOST / "DEV_LOG.jsonl").resolve()
        with mock.patch.object(G, "lf_sha", side_effect=lambda p: "0" * 64 if Path(p).resolve() == target else real(p)):
            self.assertTrue(any("DEV_LOG" in x for x in G.verify()))

    def test_changed_runtime_source_after_freeze_fails(self):
        real = G.lf_sha
        target = (G.REPO / "patches" / "r5-ets" / "argine-ets.h").resolve()
        with mock.patch.object(G, "lf_sha", side_effect=lambda p: "0" * 64 if Path(p).resolve() == target else real(p)):
            self.assertTrue(any("frozen runtime source changed" in x for x in G.verify()))

    def test_changed_criterion_fails(self):
        real = G.recompute_verdict
        def bad(h):
            r = real(h); r["criteria"] = dict(r["criteria"], scheduling_purity_1pct=False); return r
        with mock.patch.object(G, "recompute_verdict", side_effect=bad):
            self.assertTrue(any("verdict" in x for x in G.verify()))

    def test_sanitize_plain_and_json_escaped(self):
        prefix = "C:\\Users\\someone"
        raw = b'{"p": "C:\\\\Users\\\\someone\\\\Desktop\\\\x.exe"} log C:\\Users\\someone\\m.gguf C:/Users/someone/y'
        s = G.sanitize(raw, prefix)
        self.assertNotIn(b"someone", s)
        self.assertEqual(s.count(b"%USERPROFILE%"), 3)
        json.loads(s.split(b"}")[0] + b"}")                      # still valid JSON

    def test_no_personal_path_and_no_eval_id_committed(self):
        ev = [x["id"] for x in json.loads((G.P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]]
        for q in G.sealed_files():
            t = q.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(":\\Users\\", t, q)
            self.assertNotIn(":\\\\Users\\\\", t, q)
            self.assertFalse(any(i in t for i in ev), q)

    def test_identities_and_disclosure(self):
        s = json.loads((HERE / "G1_SEAL.json").read_text(encoding="utf-8"))
        self.assertEqual(s["identity"]["r5_runtime_sha256"], "da6e7183e44f6f69d58156cf683f5a929f16fc74bba442fdea39f139e4007493")
        self.assertEqual(s["identity"]["v5_runtime_sha256"], "1ed0f0037a56b14abb3b4ce6706e166c489f9166f6be6b22f491a39c1f521c12")
        self.assertIn("RECONSTRUCTED", s["v5_reconstruction_disclosure"])
        imf = json.loads((HERE / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(imf["model"]["sha256"], "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32")
        self.assertEqual(imf["store"]["sha256"], "fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146")
        for k in ("wait_primitive", "handle_release_point", "barrier_placement", "pin_release_point", "touch_order", "trace_layout"):
            self.assertIn(k, imf["implementation_choices"])

    def test_noncanonical_attempts_declared(self):
        m = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(set(m["noncanonical_directories"]), {"phase1a-host", "phase1a-host-v5rec", "ets-modelfree-v2"})
        self.assertFalse(any("v5rec" in e["path"] for e in m["files"]))


if __name__ == "__main__":
    unittest.main()
