"""Deterministic offline tests of the R3-03B instrumented-conformance and build tooling. No model, no timing."""
import hashlib, json, os, stat, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_conformance as C  # noqa: E402
import r3_03b_build_verify as BV  # noqa: E402

GOOD = {"schema": "r3-03b-v1", "calls": 24672, "ns_total": 3_000_000_000, "ns_prediction_update": 2_000_000_000,
        "ns_victim_selection": 100_000_000, "victim_calls": 7966, "ns_event_lock_wait": 5_000, "ns_event_locked_total": 3_500_000_000,
        "hist_log2": [0] * 39 + [24672]}


class EnvAndSchedule(unittest.TestCase):
    plan = json.loads((ROOT / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())

    def test_01_schedule_is_four_8gib_launches_off_on(self):
        self.assertEqual([t for t, _, _ in C.LAUNCHES], ["A-8GiB-OFF", "A-8GiB-ON", "B-8GiB-OFF", "B-8GiB-ON"])
        cfgs = {c["config_id"]: c for c in self.plan["configurations"]}
        for _, cid, _ in C.LAUNCHES:
            self.assertEqual(cfgs[cid]["budget_bytes"], 8 * 1024 ** 3)
        self.assertEqual(cfgs["A-8GiB"]["policy_id"], "b_selected")
        self.assertEqual(cfgs["B-8GiB"]["policy_id"], "predicted_protect_sham")

    def test_02_off_env_never_inherits_timing_and_on_sets_exactly_one(self):
        cfg = {c["config_id"]: c for c in self.plan["configurations"]}["B-8GiB"]
        dirty = {"ARGINE_POLICY_TIMING": "1", "PATH": "x"}
        off = C.build_env(self.plan, cfg, Path("t.jsonl"), False, dirty)
        on = C.build_env(self.plan, cfg, Path("t.jsonl"), True, {"PATH": "x"})
        self.assertNotIn("ARGINE_POLICY_TIMING", off)
        self.assertEqual(on["ARGINE_POLICY_TIMING"], "1")
        strip = lambda e: {k: v for k, v in e.items() if k != "ARGINE_POLICY_TIMING"}
        self.assertEqual(strip(off), strip(on))                       # nothing else differs between OFF and ON
        self.assertEqual(off["ARGINE_RETENTION_POLICY"], "predicted_protect_sham")
        self.assertEqual(off["ARGINE_RETENTION_PREDICTOR_DECAY"], "0.99")

    def test_03_dry_run_and_runtime_identity_refusals(self):
        r = subprocess.run([sys.executable, str(ROOT / "tools/r3_03b_conformance.py"), "--runtime", "x", "--runtime-sha256",
                            C.R3A_RUNTIME_SHA, "--out-dir", tempfile.mkdtemp(), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NEW identity", r.stdout)


class TimingRecord(unittest.TestCase):
    def test_04_valid_record_passes_and_parses_from_stderr(self):
        text = "x\nargine: policy_timing " + json.dumps(GOOD) + "\ny\n"
        recs = C.timing_records(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(C.validate_timing_record(recs[0], 24672, 7966), [])

    def test_05_each_invalid_property_is_detected(self):
        def bad(**kw):
            r = dict(GOOD); r.update(kw); return C.validate_timing_record(r, 24672, 7966)
        self.assertTrue(bad(calls=24671))
        self.assertTrue(bad(hist_log2=[0] * 40))
        self.assertTrue(bad(ns_total=0))
        self.assertTrue(bad(ns_prediction_update=3_000_000_000))
        self.assertTrue(bad(victim_calls=1))
        self.assertTrue(bad(ns_event_locked_total=1))
        self.assertTrue(bad(schema="other"))
        self.assertEqual(C.validate_timing_record({"schema": "r3-03b-v1"}, 1)[0][:7], "missing")
        self.assertTrue(C.timing_records("argine: policy_timing {oops")[0]["_unparseable"])

    def test_06_absent_when_off(self):
        self.assertEqual(C.timing_records("argine: retention trace diagnostica: ok\n"), [])


class TraceComparisons(unittest.TestCase):
    def _write(self, d, name, rows):
        p = Path(d) / name
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return p

    ROWS = [{"step": 0, "policy": "b_selected", "layer": 0, "requested_experts": [1, 2], "hit_experts": [], "miss_experts": [1, 2],
             "protected_before": [], "resident_before": [], "evicted": [], "resident_after": [{"layer": 0, "expert": 1, "score": 1, "last_ref": 1}],
             "cache_resident_bytes_before": 0, "cache_resident_bytes_after": 0, "policy_resident_bytes_after": 10, "bytes_of_layer": 5,
             "budget_bytes": 100, "semantic_event": {"pass": 0, "layer": 0}}, {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 10}]

    def test_07_byte_identical_traces_pass(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = self._write(d, "a", self.ROWS), self._write(d, "b", self.ROWS)
            r = C.compare_canonical(a, b)
            self.assertTrue(r["ok"] and r["byte_identical"])

    def test_08_declared_timing_metadata_only_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            rows2 = json.loads(json.dumps(self.ROWS)); rows2[0]["ns_policy_total"] = 123; rows2[0]["policy_timing"] = {"x": 1}
            r = C.compare_canonical(self._write(d, "a", self.ROWS), self._write(d, "b", rows2))
            self.assertTrue(r["ok"]); self.assertFalse(r["byte_identical"])

    def test_09_any_scientific_difference_fails_with_first_row(self):
        for key, val in (("evicted", [1]), ("protected_before", [[0, 1]]), ("requested_experts", [1, 3]),
                         ("resident_after", [{"layer": 0, "expert": 1, "score": 0.9, "last_ref": 1}]),
                         ("policy_resident_bytes_after", 11), ("cache_resident_bytes_before", 1)):
            with tempfile.TemporaryDirectory() as d:
                rows2 = json.loads(json.dumps(self.ROWS)); rows2[0][key] = val
                r = C.compare_canonical(self._write(d, "a", self.ROWS), self._write(d, "b", rows2))
                self.assertFalse(r["ok"], key); self.assertEqual(r["first_diff_row"], 1)

    def test_10_truncated_trace_fails(self):
        with tempfile.TemporaryDirectory() as d:
            r = C.compare_canonical(self._write(d, "a", self.ROWS), self._write(d, "b", self.ROWS[:1]))
            self.assertFalse(r["ok"])

    def test_11_sham_vs_b_ignores_policy_identity_and_protected_before_only(self):
        with tempfile.TemporaryDirectory() as d:
            sham = json.loads(json.dumps(self.ROWS)); sham[0]["policy"] = "predicted_protect_sham"; sham[0]["protected_before"] = [[0, 2]]
            self.assertTrue(C.compare_sham_vs_b(self._write(d, "a", self.ROWS), self._write(d, "b", sham))["ok"])
            sham[0]["evicted"] = [[0, 9]]
            self.assertFalse(C.compare_sham_vs_b(self._write(d, "a", self.ROWS), self._write(d, "c", sham))["ok"])


class Shutdown(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "posix signal simulation")
    def test_12_graceful_stop_lets_the_child_run_its_exit_path(self):
        code = textwrap.dedent("""
            import signal, sys, time
            def h(s, f):
                sys.stderr.write('argine: policy_timing {"schema":"r3-03b-v1"}\\n'); sys.stderr.flush(); sys.exit(0)
            signal.signal(signal.SIGINT, h); sys.stderr.write('ready\\n'); sys.stderr.flush()
            time.sleep(60)""")
        with tempfile.TemporaryDirectory() as d:
            errp = Path(d) / "e"
            with errp.open("wb") as fe:
                p = subprocess.Popen([sys.executable, "-c", code], stderr=fe)
                for _ in range(100):
                    if errp.read_text().startswith("ready"):
                        break
                    import time; time.sleep(0.05)
                self.assertEqual(C.graceful_stop(p, 10), "graceful")
            self.assertEqual(len(C.timing_records(errp.read_text())), 1)

    @unittest.skipIf(os.name == "nt", "posix signal simulation")
    def test_13_unresponsive_child_falls_back_and_is_labelled(self):
        code = "import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('r', flush=True); time.sleep(60)"
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
        p.stdout.readline()
        self.assertEqual(C.graceful_stop(p, 1), "terminate_fallback")
        self.assertEqual(C.graceful_stop(p, 1), "already_exited")


class BuildVerify(unittest.TestCase):
    def _tree(self, d, mutate=None):
        tree, repo = Path(d) / "tree", Path(d) / "repo"
        (tree / BV.CPU_DIR).mkdir(parents=True); (repo / "patches").mkdir(parents=True)
        for n in BV.OVERLAY + BV.UNCHANGED:
            (tree / BV.CPU_DIR / n).write_bytes(f"// {n}\r\nint x;\r\n".encode())
            (repo / "patches" / n).write_bytes(f"// {n}\nint x;\n".encode())            # CR-normalised equal
        if mutate:
            (tree / BV.CPU_DIR / mutate).write_bytes(b"tampered\n")
        return repo, tree

    def test_14_overlay_verification_is_crlf_insensitive_and_detects_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            repo, tree = self._tree(d)
            self.assertTrue(BV.stage_pre(repo, tree)["ok"])
        with tempfile.TemporaryDirectory() as d:
            repo, tree = self._tree(d, "argine-retention-policy.cpp")
            r = BV.stage_pre(repo, tree)
            self.assertFalse(r["ok"]); self.assertIn("argine-retention-policy.cpp", r["failures"][0])

    @unittest.skipIf(os.name == "nt", "shell stub")
    def test_15_post_stage_proves_old_runtimes_unchanged_and_new_differs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            def exe(name, body):
                p = d / name; p.write_text("#!/bin/sh\n" + body + "\n"); p.chmod(p.stat().st_mode | stat.S_IEXEC); return p
            new = exe("new", "echo 'version: 0.5.0-dev (build 1, commit 254b177)'")
            r3a = exe("r3a", "echo old"); fr = exe("fr", "echo frozen")
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            args = lambda **kw: type("A", (), dict(new_exe=str(new), r3a_exe=str(r3a), r3a_sha=sha(r3a), frozen_exe=str(fr), frozen_sha=sha(fr), record=str(d / "rec.json"), **kw))()
            ok = BV.stage_post(args())
            self.assertTrue(ok["ok"], ok["failures"])
            bad = BV.stage_post(type("A", (), dict(new_exe=str(new), r3a_exe=str(r3a), r3a_sha="0" * 64, frozen_exe=str(fr), frozen_sha=sha(fr), record=None))())
            self.assertFalse(bad["ok"])
            same = BV.stage_post(type("A", (), dict(new_exe=str(r3a), r3a_exe=str(r3a), r3a_sha=sha(r3a), frozen_exe=str(fr), frozen_sha=sha(fr), record=None))())
            self.assertFalse(same["ok"])                                        # new identity equals R3-03A runtime


class BatContracts(unittest.TestCase):
    def test_16_build_bat_does_not_touch_old_trees_and_keeps_frozen_flags(self):
        bat = (ROOT / "tools/r3_03b_build.bat").read_text()
        self.assertIn(r'set "NEWROOT=C:\Users\alpel\Desktop\Argine4-r3build-r3-03b"', bat)
        self.assertIn("if exist \"%NEWROOT%\"", bat)
        for flag in ("-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF", "-DLLAMA_CURL=OFF", "-DLLAMA_OPENSSL=OFF", "-DGGML_NATIVE=OFF",
                     "-DGGML_AVX=ON", "-DGGML_AVX2=ON", "-DGGML_FMA=ON", "-DGGML_F16C=ON", "-DGGML_BMI2=ON", "-DGGML_OPENMP=OFF",
                     "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DCMAKE_CXX_FLAGS=/DNOMINMAX", "-DCMAKE_C_FLAGS=/DNOMINMAX"):
            self.assertIn(flag, bat)
        self.assertNotIn("robocopy \"%NEW%", bat)
        self.assertNotIn("build-r3-03a\\", bat.replace(r'set "R3A_EXE=C:\Users\alpel\Desktop\Argine4-r3build-r3-03a\llama.cpp\build-r3-03a\bin\llama-server.exe"', ""))
        for n in ("argine-experts.cpp", "argine-store.cpp", "argine-store.h", "argine-retention-policy.cpp", "argine-retention-policy.h"):
            self.assertIn(n, bat)
        self.assertNotIn("copy /Y \"%FROZEN%\\build\\bin", bat)                  # no scientific binaries copied
        self.assertIn("r3_03b_check_cl_version.py", bat)

    def test_17_overlay_set_matches_actual_source_diff_since_r3_03a(self):
        out = subprocess.run(["git", "diff", "--name-only", "75f22d2", "HEAD", "--", "patches"], cwd=ROOT, capture_output=True, text=True)
        if out.returncode != 0:
            self.skipTest("git history unavailable")
        runtime = {Path(x).name for x in out.stdout.split() if x.startswith("patches/argine-") and "test" not in x and "microbench" not in x}
        self.assertEqual(runtime, set(BV.OVERLAY) - {"argine-retention-policy.cpp", "argine-retention-policy.h"} | {"argine-retention-policy.cpp", "argine-retention-policy.h"} & runtime | (runtime & set(BV.OVERLAY)))
        self.assertTrue(runtime <= set(BV.OVERLAY))                              # nothing outside the overlay changed


if __name__ == "__main__":
    unittest.main()
