"""Deterministic tests of the R3-03B microbenchmark source and analysis (no model, no authoritative timing)."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STREAM = ROOT / "experiments/R3-03B-direct-cost-attribution/routing_stream_A-4GiB.txt"
SRC = [ROOT / "patches/argine-policy-microbench.cpp", ROOT / "patches/argine-retention-policy.cpp"]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_microbench_analyze as an  # noqa: E402

ARGS = ["4294967296", "0.99", "64", "0.99", "8", "128"]


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class MicrobenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="r3_03b_mb_"))
        cls.exe = cls.tmp / "mb"
        subprocess.run(["g++", "-std=c++17", "-O1", "-I", str(ROOT / "patches"), *map(str, SRC), "-o", str(cls.exe)], check=True)
        lines = STREAM.read_text().splitlines()
        cls.head = [l for l in lines if l.startswith("#")]
        cls.body = [l for l in lines if not l.startswith("#")][:1500]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _stream(self, name, body):
        p = self.tmp / name
        p.write_text("\n".join(self.head + body) + "\n")
        return p

    def _run(self, stream, out, reps=2):
        out.mkdir()
        return subprocess.run([str(self.exe), str(stream), str(out), "1", str(reps), *ARGS], capture_output=True, text=True)

    def test_01_valid_run_produces_all_raw_files_and_report(self):
        out = self.tmp / "ok"
        r = self._run(self._stream("s.txt", self.body), out)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = {p.name for p in out.iterdir()}
        for p in ("b_selected", "predicted_protect_sham", "predicted_protect"):
            for m in ("outer", "inner"):
                self.assertIn("p1_calls_%s_%s.u32" % (p, m), names)
        self.assertEqual((out / "p1_calls_b_selected_outer.u32").stat().st_size, 2 * len(self.body) * 4)
        rows = [json.loads(l) for l in (out / "p1_replays.jsonl").read_text().splitlines()]
        rec = [x for x in rows if not x["warmup"]]
        self.assertEqual(len(rec), 2 * 10)                        # null + 3 policies x 3 modes, per repetition
        self.assertEqual(len([x for x in rows if x["warmup"]]), 2 * 10)  # 2 warmup replays per combination
        rep = an.analyse(out, expected_procs=1, expected_reps=2)
        self.assertEqual(rep["n_reps_total"], 2)
        self.assertIn("c_predictor_token_ns", rep)

    def test_02_sham_and_b_selected_trajectories_match(self):
        out = self.tmp / "traj"
        self.assertEqual(self._run(self._stream("s2.txt", self.body), out).returncode, 0)
        meta = json.loads((out / "p1_meta.json").read_text())["reference_counts"]
        self.assertEqual(meta["b_selected"], meta["predicted_protect_sham"])

    def test_03_wrong_logged_eviction_count_is_fail_closed(self):
        bad = list(self.body)
        f = bad[-1].split(" ", 6); f[4] = str(int(f[4]) + 1); bad[-1] = " ".join(f)
        r = self._run(self._stream("bad.txt", bad), self.tmp / "bad")
        self.assertEqual(r.returncode, 3)
        self.assertIn("MICROBENCH_INVALID", r.stderr)

    def test_04_analysis_rejects_incomplete_or_wrong_count(self):
        out = self.tmp / "cnt"
        self._run(self._stream("s3.txt", self.body), out)
        with self.assertRaises(SystemExit):
            an.analyse(out, expected_procs=3)
        with self.assertRaises(SystemExit):
            an.analyse(out, expected_reps=30)


class ProtocolFreezeTests(unittest.TestCase):
    def test_05_freeze_hashes_and_method(self):
        import hashlib
        d = ROOT / "experiments/R3-03B-direct-cost-attribution"
        fz = json.loads((d / "PROTOCOL_FREEZE.json").read_text())
        for name, h in fz["files_sha256"].items():
            self.assertEqual(hashlib.sha256((d / name).read_bytes()).hexdigest(), h, name)
        body = {k: v for k, v in fz.items() if k != "freeze_sha256"}
        self.assertEqual(hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), fz["freeze_sha256"])
        pr = json.loads((d / "PROTOCOL.json").read_text())
        self.assertEqual(pr["equivalence_interval"]["lower"], 0.5)
        self.assertEqual(pr["equivalence_interval"]["upper"], 2.0)
        self.assertFalse(pr["authoritative_microbenchmark"]["model_execution"])


if __name__ == "__main__":
    unittest.main()
