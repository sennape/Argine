"""R3-03A trace adapter tests on the REAL observed schema (event row, completion row, event row, ...).

Fixture = trace written by the production C++ (patches/argine-store.cpp) via the tiny-store driver; it has exactly
the alternating POLICY_EVENT / SERVICE_COMPLETION rows seen in the real A-4GiB host trace.
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests"), str(HERE)]

import r2_12_real_trace_conformance as rtc  # noqa: E402
import r3_03a_trace as tr  # noqa: E402
import test_semantic_correction as base  # noqa: E402

RTC_SHA256 = "369858c5a3aef855c9ac20e98ec0c68824242c920b0f5655780743f8dc23d11e"
NP = base.DECODE_PASSES + 1


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class TraceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.SemanticCorrectionTests.setUpClass()
        cls.inst = base.SemanticCorrectionTests("test_01_cpp_selftest")
        cls.inst.tmp = base.SemanticCorrectionTests.tmp
        for k in ("store", "binary", "stream"):
            setattr(cls.inst, k, getattr(base.SemanticCorrectionTests, k))
        cls.tmp = base.SemanticCorrectionTests.tmp
        cls.lines = cls._raw("b_selected")
        cls.lines_pp = cls._raw("predicted_protect")

    @classmethod
    def tearDownClass(cls):
        base.SemanticCorrectionTests.tearDownClass()

    @classmethod
    def _raw(cls, policy, legacy=False):
        cls.inst._drive(policy, 0.99, 64, 7, legacy=legacy)
        p = cls.tmp / f"trace_{policy}_0.99_64_7_{int(legacy)}.jsonl"
        return p.read_text().splitlines()

    def _write(self, lines, name="t.jsonl", final_newline=True):
        p = self.tmp / name
        p.write_text("\n".join(lines) + ("\n" if final_newline else ""))
        return p

    def _an(self, lines, **kw):
        return tr.analyse_file(self._write(lines), n_layers=base.N_LAYERS, expected_passes=NP, **kw)

    def _fails(self, lines, needle):
        r = self._an(lines)
        self.assertFalse(r["ok"], r)
        self.assertIn(needle, r["failure"])

    # 1
    def test_01_valid_alternating_trace(self):
        r = self._an(self.lines)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["policy_event_rows"], r["completion_rows"]), (NP * base.N_LAYERS,) * 2)
        self.assertEqual(len(self.lines), 2 * r["semantic_events"])
        self.assertEqual(r["passes"], NP)
        self.assertEqual(self._an(self.lines_pp)["r2_replay"], "MATCH")
        self.assertEqual(self._an(self._raw("predicted_protect_sham"), sham=True)["r2_replay"], "MATCH")

    # 2
    def test_02_policy_rows_reach_semantic_replay(self):
        r = self._an(self.lines_pp)
        self.assertEqual(r["protected_before_compared_events"], r["semantic_events"])
        bad = list(self.lines_pp)
        row = json.loads(bad[20]); row["evicted"] = row["evicted"] + [{"layer": 0, "expert": 99}]
        bad[20] = json.dumps(row)
        self._fails(bad, "R2 replay mismatch")
        bad = list(self.lines_pp)
        row = json.loads(bad[20]); row["resident_after"][0]["score"] += 1.0
        bad[20] = json.dumps(row)
        self._fails(bad, "mismatch")

    # 3
    def test_03_completion_rows_are_not_policy_events(self):
        self.assertEqual(tr.classify_row(json.loads(self.lines[1])), tr.SERVICE_COMPLETION)
        self.assertEqual(tr.classify_row(json.loads(self.lines[0])), tr.POLICY_EVENT)
        with self.assertRaises(rtc.TraceFormatError):      # the historical parser still rejects them (unchanged)
            rtc.parse_trace(self.lines[:2])
        self.assertTrue(self._an(self.lines)["ok"])

    # 4
    def test_04_malformed_completion_fails(self):
        for mut in (lambda c: c.update(complete=2), lambda c: c.update(complete=True),
                    lambda c: c.update(cache_resident_bytes_post_service=-1),
                    lambda c: c.update(cache_resident_bytes_post_service="x"), lambda c: c.update(step="0"),
                    lambda c: c.update(extra=1)):
            lines = list(self.lines)
            c = json.loads(lines[1]); mut(c); lines[1] = json.dumps(c)
            r = self._an(lines)
            self.assertFalse(r["ok"], c)
        lines = list(self.lines); c = json.loads(lines[1]); del c["complete"]; lines[1] = json.dumps(c)
        self.assertFalse(self._an(lines)["ok"])
        lines = list(self.lines); c = json.loads(lines[3]); c["step"] = 99; lines[3] = json.dumps(c)
        self._fails(lines, "completion step")

    # 5
    def test_05_unknown_row_fails_closed(self):
        for junk in ('{"foo": 1}', "[1, 2]", '{"step": 1}', "not json", '{"step":0,"complete":1}'):
            lines = list(self.lines); lines.insert(2, junk)
            self.assertFalse(self._an(lines)["ok"], junk)
        self.assertEqual(tr.classify_row({"foo": 1}), tr.UNKNOWN)
        self.assertEqual(tr.classify_row([1]), tr.UNKNOWN)

    # 6
    def test_06_orphan_completion_fails(self):
        lines = list(self.lines)
        lines.insert(0, json.dumps({"step": 0, "complete": 1, "cache_resident_bytes_post_service": 1}))
        self._fails(lines, "orphan")

    # 7
    def test_07_duplicate_completion_fails(self):
        lines = list(self.lines); lines.insert(2, lines[1])
        self._fails(lines, "duplicate completion")

    # 8
    def test_08_missing_completion_fails(self):
        lines = list(self.lines); del lines[5]
        self._fails(lines, "missing completion")
        self._fails(self.lines[:-1], "missing completion")          # final completion absent
        # truncated (non-terminated) final row
        p = self._write(self.lines, "trunc.jsonl", final_newline=False)
        r = tr.analyse_file(p, n_layers=base.N_LAYERS, expected_passes=NP)
        self.assertFalse(r["ok"]); self.assertIn("truncated", r["failure"])

    # 9
    def test_09_historical_r2_parser_unchanged(self):
        import hashlib
        self.assertEqual(hashlib.sha256((ROOT / "tools" / "r2_12_real_trace_conformance.py").read_bytes()).hexdigest(),
                         RTC_SHA256)
        legacy = [l for l in self._raw("b_selected", legacy=True) if '"complete"' not in l]
        events = rtc.parse_trace(legacy)
        ok, mismatch, _ = rtc.compare(events)                      # pure R2-style rows: legacy path untouched
        self.assertTrue(ok, mismatch)
        self.assertEqual(len(events), 3 * NP * base.N_LAYERS)

    # 10
    def test_10_semantic_event_fields_required(self):
        def mut(i, f):
            lines = list(self.lines); r = json.loads(lines[i]); f(r); lines[i] = json.dumps(r); return lines
        self._fails(mut(0, lambda r: r.pop("semantic_event")), "semantic_event")
        self._fails(mut(0, lambda r: r["semantic_event"].update(schema="x")), "semantic_event")
        self._fails(mut(0, lambda r: r["semantic_event"].update(first_node=0)), "semantic_event")
        self._fails(mut(0, lambda r: r["semantic_event"].pop("pass")), "semantic_event")
        self._fails(mut(2, lambda r: r["semantic_event"].update(layer=0)), "semantic_event")

    # extra structural guards
    def test_11_duplicate_missing_and_budget_and_provenance(self):
        lines = list(self.lines); lines[2:4] = lines[0:2]            # repeat event 0 in place of event 1
        self.assertFalse(self._an(lines)["ok"])
        lines = list(self.lines); del lines[6:8]                     # drop a whole event (+completion)
        self.assertFalse(self._an(lines)["ok"])
        lines = list(self.lines); r = json.loads(lines[10]); r["policy_resident_bytes_after"] = r["budget_bytes"] + 1
        lines[10] = json.dumps(r)
        self._fails(lines, "budget violation")
        lines = list(self.lines); r = json.loads(lines[10]); r["policy_params"]["decay_factor"] = 0.5
        lines[10] = json.dumps(r)
        self._fails(lines, "provenance")

    def test_12_checkpoint_resume_equals_full(self):
        p = self._write(self.lines, "ck.jsonl")
        full = tr.analyse_file(p, n_layers=base.N_LAYERS, expected_passes=NP)
        ck = self.tmp / "ck.pkl"
        if ck.exists():
            ck.unlink()
        steps = 0
        while True:
            r = tr.analyse_file(p, n_layers=base.N_LAYERS, expected_passes=NP, checkpoint=ck, max_seconds=0.0005)
            steps += 1
            if not r.get("in_progress"):
                break
            self.assertLess(steps, 5000)
        self.assertTrue(r["ok"], r)
        self.assertGreater(steps, 1)
        for k in ("semantic_events", "r2_replay", "total_evictions", "decay_score_max_abs_diff", "passes"):
            self.assertEqual(r[k], full[k], k)

    # driver: explicit, recorded, never-overwriting resume (no model, mocked plan)
    def test_13_driver_resume_logic(self):
        import contextlib
        import hashlib
        import io
        from unittest import mock
        import r3_03a_conformance as cf
        d = self.tmp / "drv"; d.mkdir(exist_ok=True)
        (d / "rt.exe").write_bytes(b"fake-runtime"); (d / "m.gguf").write_bytes(b"m"); (d / "s.psxs").write_bytes(b"s")
        rt_sha = hashlib.sha256(b"fake-runtime").hexdigest()
        plan = {"model": {"path": str(d / "m.gguf")}, "store": {"path": str(d / "s.psxs")},
                "configurations": [{"config_id": "A-4GiB", "policy_id": "b_selected", "budget_bytes": 1, "params": {}}]}
        (d / "plan.json").write_text(json.dumps(plan))

        def run(out, *extra):
            err = io.StringIO()
            with mock.patch.object(cf, "PLAN", d / "plan.json"), mock.patch.object(cf, "LAUNCHES", ("A-4GiB",)), \
                    mock.patch.object(cf, "N_LAYERS", base.N_LAYERS), mock.patch.object(cf, "FROZEN_PASSES", NP), \
                    mock.patch.object(cf, "FROZEN_TOKEN_DIGEST", "D"), mock.patch.object(cf, "N_PREDICT", 3), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = cf.main(["--runtime", str(d / "rt.exe"), "--runtime-sha256", rt_sha, "--out-dir", str(out), *extra])
            return rc, err.getvalue()

        def make(out, with_launch=True, digest="D"):
            out.mkdir(exist_ok=True)
            tp = out / "A-4GiB.trace.jsonl"
            tp.write_text("\n".join(self.lines) + "\n")
            (out / "A-4GiB.stderr.log").write_text("ok")
            if with_launch:
                (out / "A-4GiB.launch.json").write_text(json.dumps(
                    {"config_id": "A-4GiB", "n_tokens": 3, "output_token_digest": digest, "stderr_path": str(out / "A-4GiB.stderr.log"),
                     "trace_bytes": tp.stat().st_size, "trace_sha256": cf.sha256_file(tp)}))
            return tp

        o1 = d / "o1"; make(o1)
        rc, err = run(o1); self.assertEqual(rc, 1); self.assertIn("refusing to overwrite", err)       # no silent reuse
        before = (o1 / "A-4GiB.trace.jsonl").read_bytes()
        rc, err = run(o1, "--resume"); self.assertEqual(rc, 0, err)
        res = json.loads((o1 / "CONFORMANCE_RESULT.json").read_text())
        self.assertTrue(res["launches"]["A-4GiB"]["reused"]); self.assertTrue(res["resume"])
        self.assertEqual(res["result"], "R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_PASS")
        self.assertEqual((o1 / "A-4GiB.trace.jsonl").read_bytes(), before)                            # never modified
        o2 = d / "o2"; make(o2, with_launch=False)                                                    # trace without token evidence
        rc, err = run(o2, "--resume"); self.assertEqual(rc, 1); self.assertIn("NOT reusable", err)
        o3 = d / "o3"; tp = make(o3); tp.write_text(tp.read_text() + "\n")                            # tampered trace
        rc, err = run(o3, "--resume"); self.assertEqual(rc, 1); self.assertIn("invalid", err)
        o4 = d / "o4"; make(o4, digest="X")                                                           # wrong token digest
        rc, err = run(o4, "--resume"); self.assertEqual(rc, 1)
        res = json.loads((o4 / "CONFORMANCE_RESULT.json").read_text())
        self.assertEqual(res["result"], "R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_FAIL")


if __name__ == "__main__":
    unittest.main()
