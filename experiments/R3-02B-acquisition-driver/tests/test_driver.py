"""R3-02B acquisition-driver tests. Mocks only; no model, no process launch."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PHASE = REPO / "experiments" / "R3-02-calibration-mechanism"
sys.path.insert(0, str(REPO / "tools"))

import r3_02_acquire as A  # noqa: E402

PLAN = A.load_json(PHASE / "ACQUISITION_PLAN.json")
PREREG = A.load_json(PHASE / "PREREGISTRATION.json")


def _state(tmp: Path) -> A.State:
    return A.State.load(tmp / "state.json")


CLEAN_EVIDENCE = {"schema": 3, "request_completed_successfully": True, "server_return_code": 0,
                  "exit_code": 0, "intentional_shutdown": True, "shutdown_verified": True,
                  "shutdown_mode": "terminate", "kill_used": False,
                  "process_alive_before_shutdown": True, "stderr_error_markers": [],
                  "admission": "ADMISSIBLE", "output_token_digest": "a" * 64,
                  "budget_invariant_ok": True}


def _ok_run(launch):
    return {"admitted": True, "scientific_status": "CELL_READY", "evidence": dict(CLEAN_EVIDENCE)}


class TestFrozenPlan(unittest.TestCase):
    def test_plan_validates(self):
        self.assertEqual(A.validate_frozen(PLAN, PREREG), [])

    def test_protocol_digest_matches(self):
        self.assertEqual(A.protocol_digest(PLAN, PREREG), A.PROTOCOL_DIGEST)

    def test_tampered_plan_stops(self):
        bad = copy.deepcopy(PLAN)
        bad["launches"][3]["n_predict"] = 256
        self.assertTrue(any("digest" in f for f in A.validate_frozen(bad, PREREG)))

    def test_preflight_stage_has_three(self):
        self.assertEqual(len(A.stage_launches(PLAN, "preflight")), 3)

    def test_scientific_stage_has_49(self):
        self.assertEqual(len(A.stage_launches(PLAN, "scientific")), 49)

    def test_kind_counts(self):
        sci = A.stage_launches(PLAN, "scientific")
        self.assertEqual(sum(1 for l in sci if l["kind"] == "TRACE_COUNT"), 7)
        self.assertEqual(sum(1 for l in sci if l["kind"] == "TIMING_WARMUP"), 7)
        self.assertEqual(sum(1 for l in sci if l["kind"] == "TIMING_COUNTED"), 35)

    def test_cells_expected(self):
        self.assertEqual(A.scientific_cells(PLAN), 35)

    def test_trace_reuse_one_to_five(self):
        tc = {l["config_id"] for l in PLAN["launches"] if l["kind"] == "TRACE_COUNT"}
        counted = {}
        for l in PLAN["launches"]:
            if l["kind"] == "TIMING_COUNTED":
                counted[l["config_id"]] = counted.get(l["config_id"], 0) + 1
        self.assertEqual(tc, set(counted))
        self.assertEqual(set(counted.values()), {5})

    def test_no_reuse_across_configurations(self):
        by_cfg = {}
        for l in PLAN["launches"]:
            if l["kind"] == "TRACE_COUNT":
                by_cfg.setdefault(l["config_id"], []).append(l["seq"])
        self.assertTrue(all(len(v) == 1 for v in by_cfg.values()))

    def test_sequence_is_deterministic(self):
        self.assertEqual([l["seq"] for l in PLAN["launches"]], list(range(1, 53)))
        self.assertEqual([l["seq"] for l in A.stage_launches(PLAN, "preflight")], [1, 2, 3])


class TestIdentityFailClosed(unittest.TestCase):
    def _plan_with(self, tmp: Path, runtime_bytes=b"rt", model_bytes=b"m", store_bytes=b"s", wl_bytes=b"w"):
        rt, mp, sp, wp = (tmp / n for n in ("rt.bin", "model.bin", "store.bin", "wl.json"))
        rt.write_bytes(runtime_bytes); mp.write_bytes(model_bytes)
        sp.write_bytes(store_bytes); wp.write_bytes(wl_bytes)
        plan = copy.deepcopy(PLAN)
        plan["runtime"]["path"] = str(rt); plan["runtime"]["sha256"] = hashlib.sha256(runtime_bytes).hexdigest()
        plan["model"]["path"] = str(mp); plan["model"]["sha256"] = hashlib.sha256(model_bytes).hexdigest()
        plan["store"]["path"] = str(sp); plan["store"]["sha256"] = hashlib.sha256(store_bytes).hexdigest()
        plan["workload"]["path"] = str(wp); plan["workload"]["sha256"] = hashlib.sha256(wl_bytes).hexdigest()
        return plan, (rt, mp, sp, wp)

    def test_identities_ok(self):
        with tempfile.TemporaryDirectory() as d:
            plan, _ = self._plan_with(Path(d))
            self.assertEqual(A.verify_identities(plan), [])

    def test_runtime_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            plan, (rt, *_rest) = self._plan_with(Path(d))
            rt.write_bytes(b"tampered")
            self.assertTrue(any("runtime" in f for f in A.verify_identities(plan)))

    def test_model_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            plan, (_rt, mp, *_rest) = self._plan_with(Path(d))
            mp.write_bytes(b"tampered")
            self.assertTrue(any("model" in f for f in A.verify_identities(plan)))

    def test_store_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            plan, (_rt, _mp, sp, _wp) = self._plan_with(Path(d))
            sp.write_bytes(b"tampered")
            self.assertTrue(any("store" in f for f in A.verify_identities(plan)))

    def test_missing_attestation_refused(self):
        with tempfile.TemporaryDirectory() as d:
            rc = A.main(["--plan", str(PHASE / "ACQUISITION_PLAN.json"), "--stage", "preflight",
                         "--skip-file-hashes", "--out-dir", d])
            self.assertEqual(rc, 3)


class TestExecution(unittest.TestCase):
    def _run(self, tmp, *, run_one=_ok_run, pair_one=None, stage="preflight", pre_state=None):
        st = _state(tmp)
        if pre_state:
            pre_state(st)
        summary = A.execute_stage(PLAN, PREREG, stage, st, out_dir=tmp, runner=None,
                                  sleep=lambda s: sleeps.append(s),
                                  free_ram_check=lambda b: None,
                                  run_one=run_one, pair_one=pair_one,
                                  storage_check=False)
        return summary, st

    def setUp(self):
        global sleeps
        sleeps = []

    def test_preflight_runs_three_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            order = []

            def run(launch):
                order.append(launch["seq"])
                return _ok_run(launch)

            summary, st = self._run(Path(d), run_one=run)
            self.assertEqual(order, [1, 2, 3])
            self.assertEqual(summary["stage_status"], "SUCCEEDED")

    def test_cooldown_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            self._run(Path(d))
            self.assertEqual(sleeps, [60, 60, 60])

    def test_no_concurrency(self):
        with tempfile.TemporaryDirectory() as d:
            active = {"n": 0, "max": 0}

            def run(launch):
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
                active["n"] -= 1
                return _ok_run(launch)

            self._run(Path(d), run_one=run)
            self.assertEqual(active["max"], 1)

    def test_failure_then_success_retries_once(self):
        with tempfile.TemporaryDirectory() as d:
            calls = {"n": 0}

            def run(launch):
                calls["n"] += 1
                if launch["seq"] == 2 and calls["n"] == 2:
                    raise A.LaunchFailure("transient")
                return _ok_run(launch)

            summary, st = self._run(Path(d), run_one=run)
            self.assertEqual(st.launches["2"]["attempts"], 2)
            self.assertEqual(st.status(2), "SUCCEEDED")

    def test_second_failure_inadmissible(self):
        with tempfile.TemporaryDirectory() as d:
            def run(launch):
                if launch["seq"] == 2:
                    raise A.LaunchFailure("persistent")
                return _ok_run(launch)

            summary, st = self._run(Path(d), run_one=run)
            self.assertEqual(st.status(2), "INADMISSIBLE")
            self.assertEqual(st.launches["2"]["attempts"], 2)
            self.assertEqual(summary["stage_status"], "FAILED")

    def test_resume_skips_completed(self):
        with tempfile.TemporaryDirectory() as d:
            def pre(st):
                st.mark(1, "SUCCEEDED", attempts=1)
                st.save()

            calls = []
            def run(launch):
                calls.append(launch["seq"])
                return _ok_run(launch)

            summary, _ = self._run(Path(d), run_one=run, pre_state=pre)
            self.assertNotIn(1, calls)
            self.assertEqual(summary["skipped"], 1)

    def test_partial_record_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            def pre(st):
                st.mark(1, "RUNNING", attempts=1)
                st.save()

            calls = []
            def run(launch):
                calls.append(launch["seq"])
                return _ok_run(launch)

            _, st = self._run(Path(d), run_one=run, pre_state=pre)
            self.assertIn(1, calls)
            self.assertEqual(st.status(1), "SUCCEEDED")

    def test_offline_fixture_cannot_enter_scientific(self):
        with tempfile.TemporaryDirectory() as d:
            def pre(st):
                st.stage_status["preflight"] = "SUCCEEDED"
                for k in (1, 2, 3):
                    st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                           "evidence": {"schema": 3, "request_completed_successfully": True, "server_return_code": 0, "intentional_shutdown": True, "shutdown_verified": True, "shutdown_mode": "terminate", "kill_used": False, "process_alive_before_shutdown": True, "stderr_error_markers": [], "admission": "ADMISSIBLE", "exit_code": 0,
                                                        "output_token_digest": "a" * 64,
                                                        "budget_invariant_ok": True}}
                st.save()

            def run(launch):
                return {"admitted": True, "scientific_status": "OFFLINE_FIXTURE"}

            summary, st = self._run(Path(d), run_one=run, stage="scientific", pre_state=pre)
            self.assertGreater(summary["inadmissible"], 0)
            self.assertEqual(summary["stage_status"], "FAILED")

    def test_pair_failure_marks_inadmissible(self):
        with tempfile.TemporaryDirectory() as d:
            def pre(st):
                st.stage_status["preflight"] = "SUCCEEDED"
                for k in (1, 2, 3):
                    st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                           "evidence": {"schema": 3, "request_completed_successfully": True, "server_return_code": 0, "intentional_shutdown": True, "shutdown_verified": True, "shutdown_mode": "terminate", "kill_used": False, "process_alive_before_shutdown": True, "stderr_error_markers": [], "admission": "ADMISSIBLE", "exit_code": 0,
                                                        "output_token_digest": "a" * 64,
                                                        "budget_invariant_ok": True}}
                st.save()

            summary, st = self._run(Path(d), stage="scientific", pre_state=pre,
                                    pair_one=lambda l, r: l["kind"] != "TIMING_COUNTED")
            self.assertEqual(summary["inadmissible"], 35)
            self.assertEqual(st.status(52), "INADMISSIBLE")

    def test_preflight_failure_blocks_scientific(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(Path(d))
            st.stage_status["preflight"] = "FAILED"
            st.save()
            with self.assertRaises(RuntimeError):
                A.execute_stage(PLAN, PREREG, "scientific", st, out_dir=Path(d), runner=None,
                                sleep=lambda s: None, free_ram_check=lambda b: None,
                                run_one=_ok_run, storage_check=False)

    def test_scientific_runs_49(self):
        with tempfile.TemporaryDirectory() as d:
            def pre(st):
                st.stage_status["preflight"] = "SUCCEEDED"
                for k in (1, 2, 3):
                    st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                           "evidence": {"schema": 3, "request_completed_successfully": True, "server_return_code": 0, "intentional_shutdown": True, "shutdown_verified": True, "shutdown_mode": "terminate", "kill_used": False, "process_alive_before_shutdown": True, "stderr_error_markers": [], "admission": "ADMISSIBLE", "exit_code": 0,
                                                        "output_token_digest": "a" * 64,
                                                        "budget_invariant_ok": True}}
                st.save()

            seqs = []
            def run(launch):
                seqs.append(launch["seq"])
                return _ok_run(launch)

            summary, _ = self._run(Path(d), run_one=run, stage="scientific", pre_state=pre,
                                   pair_one=lambda l, r: True)
            self.assertEqual(len(seqs), 49)
            self.assertEqual(seqs, [l["seq"] for l in A.stage_launches(PLAN, "scientific")])
            self.assertEqual(summary["stage_status"], "SUCCEEDED")


class TestDryRunAndBoundaries(unittest.TestCase):
    def test_dry_run_valid_and_52(self):
        report = A.dry_run(PLAN, PREREG, "scientific", hash_files=False)
        self.assertEqual(report["status"], "VALID")
        self.assertEqual(report["launches"], 49)
        report_pre = A.dry_run(PLAN, PREREG, "preflight", hash_files=False)
        self.assertEqual(report_pre["launches"], 3)
        self.assertEqual(report_pre["launches"] + report["launches"], 52)

    def test_dry_run_never_starts_process(self):
        class Boom(Exception):
            pass

        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(Boom())
        try:
            report = A.dry_run(PLAN, PREREG, "scientific", hash_files=False)
        finally:
            A.subprocess.Popen = original
        self.assertEqual(report["status"], "VALID")

    def test_no_eval_references(self):
        blob = (PHASE / "ACQUISITION_PLAN.json").read_text(encoding="utf-8")
        blob += (PHASE / "PREREGISTRATION.json").read_text(encoding="utf-8")
        blob += (REPO / "tools" / "r3_02_acquire.py").read_text(encoding="utf-8")
        self.assertNotIn("EVAL", blob)
        self.assertNotIn("held-out", blob)


class TestLiveExecutionPath(unittest.TestCase):
    """Regression for the 2026-11-17 live failure: main() referenced
    execute_stage before it was defined, because the engine had been appended
    after the __main__ guard. Import-based tests could not see it."""

    def test_module_defines_every_symbol_main_uses(self):
        for name in ("main", "execute_stage", "validate_frozen", "verify_identities",
                     "dry_run", "stage_launches", "load_json", "protocol_digest",
                     "State", "SubprocessRunner", "LaunchFailure", "scientific_cells"):
            self.assertTrue(hasattr(A, name), f"driver does not define {name}")

    def test_main_guard_is_the_last_top_level_statement(self):
        import ast
        tree = ast.parse((REPO / "tools" / "r3_02_acquire.py").read_text(encoding="utf-8"))
        last = tree.body[-1]
        self.assertIsInstance(last, ast.If)
        self.assertIn("__name__", ast.dump(last.test))

    def test_cli_script_mode_dry_run_preflight(self):
        import subprocess as sp
        proc = sp.run([sys.executable, str(REPO / "tools" / "r3_02_acquire.py"),
                       "--plan", str(PHASE / "ACQUISITION_PLAN.json"),
                       "--stage", "preflight", "--dry-run", "--skip-file-hashes"],
                      capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        report = json.loads(proc.stdout)
        self.assertEqual(report["status"], "VALID")
        self.assertEqual(report["launches"], 3)

    def test_cli_script_mode_dry_run_scientific(self):
        import subprocess as sp
        proc = sp.run([sys.executable, str(REPO / "tools" / "r3_02_acquire.py"),
                       "--plan", str(PHASE / "ACQUISITION_PLAN.json"),
                       "--stage", "scientific", "--dry-run", "--skip-file-hashes"],
                      capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        report = json.loads(proc.stdout)
        self.assertEqual(report["launches"], 49)

    def test_main_mocked_execution_reaches_orchestration(self):
        import tempfile
        calls = []

        class FakeRunner:
            def __init__(self, plan, out_dir):
                self.plan = plan

            def run_launch(self, launch, *, trace_path=None):
                calls.append(launch["seq"])
                return {"admitted": True, "scientific_status": "CELL_READY",
                        "tokens": [1, 2, 3]}

        original_runner = A.SubprocessRunner
        original_ram = A.default_free_ram_check
        original_sleep = A.time.sleep
        original_storage = A.storage_gate
        A.SubprocessRunner = FakeRunner
        A.default_free_ram_check = lambda budget: None
        A.time.sleep = lambda s: None
        A.storage_gate = lambda *a, **k: []
        original_run_one = A._default_run_one
        def _fake_run_one(launch, plan, out_dir, runner):
            calls.append(launch["seq"])
            return {"admitted": True, "evidence": {"schema": 3, "request_completed_successfully": True, "server_return_code": 0, "intentional_shutdown": True, "shutdown_verified": True, "shutdown_mode": "terminate", "kill_used": False, "process_alive_before_shutdown": True, "stderr_error_markers": [], "admission": "ADMISSIBLE", "exit_code": 0,
                                                   "output_token_digest": "b" * 64,
                                                   "budget_invariant_ok": True}}
        A._default_run_one = _fake_run_one
        try:
            with tempfile.TemporaryDirectory() as d:
                rc = A.main(["--plan", str(PHASE / "ACQUISITION_PLAN.json"),
                             "--stage", "preflight", "--attest-clean-environment",
                             "--skip-file-hashes", "--out-dir", d])
                self.assertEqual(rc, 0)
                state = json.loads((Path(d) / "state.json").read_text(encoding="utf-8"))
                statuses = [state["launches"][str(k)]["status"] for k in (1, 2, 3)]
                self.assertEqual(statuses, ["SUCCEEDED"] * 3)
                self.assertEqual(state["stage_status"]["preflight"], "SUCCEEDED")
        finally:
            A.SubprocessRunner = original_runner
            A.default_free_ram_check = original_ram
            A.time.sleep = original_sleep
            A.storage_gate = original_storage
            A._default_run_one = original_run_one
        self.assertEqual(calls, [1, 2, 3])


class TestProjectImportResolution(unittest.TestCase):
    """Regression for the 2026-11-17 live failure: `python tools/r3_02_acquire.py`
    put tools/ (not the repo root) on sys.path, so `import argine` raised
    ModuleNotFoundError. Tests had passed only because they inserted paths."""

    def test_real_script_from_repo_root(self):
        import subprocess as sp
        proc = sp.run([sys.executable, str(REPO / "tools" / "r3_02_acquire.py"),
                       "--check-imports", "--stage", "preflight"],
                      capture_output=True, text=True, cwd=str(REPO),
                      env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        info = json.loads(proc.stdout)
        self.assertTrue(info["ok"])
        self.assertTrue(info["inside_repo"])
        self.assertTrue(info["sys_path_has_repo"])

    def test_real_script_from_other_cwd(self):
        import subprocess as sp
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            proc = sp.run([sys.executable, str(REPO / "tools" / "r3_02_acquire.py"),
                           "--check-imports", "--stage", "preflight"],
                          capture_output=True, text=True, cwd=d,
                          env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            info = json.loads(proc.stdout)
            self.assertTrue(info["inside_repo"])

    def test_no_pythonpath_required(self):
        import subprocess as sp
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = sp.run([sys.executable, str(REPO / "tools" / "r3_02_acquire.py"),
                       "--check-imports", "--stage", "preflight"],
                      capture_output=True, text=True, cwd=str(REPO), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

    def test_planner_resolves_inside_this_repository(self):
        path = Path(A.planner_module_path()).resolve()
        self.assertEqual(path, (REPO / "argine" / "planner.py").resolve())
        self.assertIn(str(REPO), str(path))

    def test_gate_reaches_real_planner_module(self):
        planner = A._load_canonical_planner()
        self.assertTrue(hasattr(planner, "plan"))
        self.assertTrue(hasattr(planner, "PlanRequest"))
        self.assertIn(str(REPO), str(Path(planner.__file__).resolve()))

    def test_gate_wiring_uses_frozen_budget_and_context(self):
        captured = {}

        class StubPlanner:
            @staticmethod
            def PlanRequest(**kwargs):
                captured["req"] = kwargs
                return kwargs

            @staticmethod
            def plan(model, hw, req):
                class R:
                    feasible = True
                return R()

        original_loader = A._load_canonical_planner
        original_inputs = A._load_gate_inputs
        A._load_canonical_planner = lambda: StubPlanner
        A._load_gate_inputs = lambda p: (object(), object())
        try:
            A.default_free_ram_check(4 * 1024 ** 3, model_path=Path("ignored.gguf"))
        finally:
            A._load_canonical_planner = original_loader
            A._load_gate_inputs = original_inputs
        req = captured["req"]
        self.assertEqual(req["expert_store"], True)
        self.assertEqual(req["expert_store_budget"], 4 * 1024 ** 3)
        self.assertEqual(req["ctx"], 4096)
        self.assertEqual(req["backend"], "cpu")

    def test_gate_fails_closed_when_planner_unavailable(self):
        original = A._load_canonical_planner
        A._load_canonical_planner = lambda: (_ for _ in ()).throw(ImportError("no argine"))
        try:
            with self.assertRaises(A.LaunchFailure) as ctx:
                A.default_free_ram_check(4 * 1024 ** 3)
        finally:
            A._load_canonical_planner = original
        self.assertIn("unavailable", str(ctx.exception))

    def test_gate_fails_closed_when_infeasible(self):
        class StubPlanner:
            @staticmethod
            def PlanRequest(**kwargs):
                return kwargs

            @staticmethod
            def plan(model, hw, req):
                class R:
                    feasible = False
                return R()

        original_loader = A._load_canonical_planner
        original_inputs = A._load_gate_inputs
        A._load_canonical_planner = lambda: StubPlanner
        A._load_gate_inputs = lambda p: (object(), object())
        try:
            with self.assertRaises(A.LaunchFailure):
                A.default_free_ram_check(4 * 1024 ** 3)
        finally:
            A._load_canonical_planner = original_loader
            A._load_gate_inputs = original_inputs

    def test_gate_never_starts_llama_server(self):
        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("server started"))
        try:
            A.planner_module_path()
        finally:
            A.subprocess.Popen = original


class TestWorkloadAndEvidence(unittest.TestCase):
    """Part A/B regression: canonical prompt + complete R3-01 evidence persistence."""

    def setUp(self):
        self.plan = A.load_json(PHASE / "ACQUISITION_PLAN.json")
        self.cfg = next(c for c in self.plan["configurations"] if c["config_id"] == "A-4GiB")
        self.launch = next(l for l in self.plan["launches"]
                           if l["config_id"] == "A-4GiB" and l["kind"] == "TRACE_COUNT")

    def _make_trace(self, path, tokens):
        rows = [
            {"step": 0, "policy": self.cfg["policy_id"], "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [1, 2, 3], "hit_experts": [], "miss_experts": [1, 2, 3],
             "protected_before": [], "resident_before": [], "evicted": [],
             "resident_after": [{"layer": 0, "expert": e} for e in (1, 2, 3)],
             "cache_resident_bytes_before": 0, "cache_resident_bytes_after": 300,
             "policy_resident_bytes_after": 300, "budget_bytes": self.cfg["budget_bytes"],
             "policy_params": self.cfg["params"]},
            {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 300},
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return tokens

    class _Runner:
        def __init__(self, plan, out_dir, tokens):
            self.plan, self.out_dir, self.tokens = plan, out_dir, tokens

        def run_launch(self, launch, *, trace_path=None):
            log_dir = self.out_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            err = log_dir / f"{launch['seq']:02d}.stderr.log"
            err.write_text("argine: ok\n", encoding="utf-8")
            out = log_dir / f"{launch['seq']:02d}.stdout.log"
            out.write_text("ok\n", encoding="utf-8")
            return {"tokens": list(self.tokens), "timings": {"prompt_ms": 500, "predicted_ms": 1000},
                    "wall_seconds": 1.5, "prompt_seconds": 0.5, "predicted_seconds": 1.0,
                    "exit_code": 0, "stdout_path": str(out), "stderr_path": str(err),
                    "process_cpu_seconds": 0.25}

    def test_canonical_prompt_non_empty_and_exact(self):
        info = A.load_workload_prompt(self.plan)
        doc = json.loads((REPO / self.plan["workload"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(info["prompt"], doc["items"][0]["prompt"])
        self.assertGreater(len(info["prompt"]), 100)

    def test_workload_sha_unchanged(self):
        info = A.load_workload_prompt(self.plan)
        self.assertEqual(info["sha256"],
                         "c02f9657d12d874cc43523e2b609bb604a68a6a8b267b2ffac6d3420c509b730")
        self.assertEqual(hashlib.sha256((REPO / self.plan["workload"]["path"]).read_bytes()).hexdigest(),
                         info["sha256"])

    def test_unsupported_schema_fails_closed(self):
        original_json, original_sha = A.load_json, A.sha256_file
        A.load_json = lambda p: {"items": []}
        A.sha256_file = lambda p: self.plan["workload"]["sha256"]
        try:
            with self.assertRaises(A.LaunchFailure):
                A.load_workload_prompt(self.plan)
        finally:
            A.load_json, A.sha256_file = original_json, original_sha
        A.load_json = lambda p: {"items": [{"id": "x"}]}
        A.sha256_file = lambda p: self.plan["workload"]["sha256"]
        try:
            with self.assertRaises(A.LaunchFailure):
                A.load_workload_prompt(self.plan)
        finally:
            A.load_json, A.sha256_file = original_json, original_sha

    def test_trace_count_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            runner = self._Runner(self.plan, d, tokens=[1, 2, 3])
            tp = d / "traces" / "A-4GiB.jsonl"
            self._make_trace(tp, [1, 2, 3])
            res = A._default_run_one(self.launch, self.plan, d, runner)
            self.assertTrue((d / "records" / "trace_count-A-4GiB.json").exists())
            self.assertTrue((d / "admissions" / "trace_count-A-4GiB.json").exists())
            self.assertEqual(res["evidence"]["schema"], 3)
            self.assertEqual(len(res["evidence"]["output_token_digest"]), 64)
            self.assertEqual(res["evidence"]["trace_sha256"],
                             res["record"]["telemetry"]["trace_sha256"])
            self.assertEqual(res["evidence"]["exit_code"], 0)
            self.assertEqual(res["evidence"]["budget_violations"], 0)
            self.assertTrue(res["evidence"]["budget_invariant_ok"])

    def test_timing_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            launch = next(l for l in self.plan["launches"]
                          if l["config_id"] == "A-4GiB" and l["kind"] == "TIMING_COUNTED")
            runner = self._Runner(self.plan, d, tokens=[1, 2, 3])
            res = A._default_run_one(launch, self.plan, d, runner)
            files = list((d / "records").glob("timing-A-4GiB-r*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(res["record"]["timing"]["tokens_generated"], 3)
            self.assertEqual(res["record"]["timing"]["ttft_seconds"], 0.5)
            self.assertEqual(res["admission"]["status"], "ADMISSIBLE")

    def test_cell_persistence_and_trace_reference(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            launch = next(l for l in self.plan["launches"]
                          if l["config_id"] == "A-4GiB" and l["kind"] == "TIMING_COUNTED")
            runner = self._Runner(self.plan, d, tokens=[1, 2, 3])
            tp = d / "traces" / "A-4GiB.jsonl"
            self._make_trace(tp, [1, 2, 3])
            A._default_run_one(self.launch, self.plan, d, runner)
            res = A._default_run_one(launch, self.plan, d, runner)
            self.assertTrue(A._default_pair(launch, res, self.plan, d))
            cells = list((d / "cells").glob("cell-A-4GiB-r*.json"))
            self.assertEqual(len(cells), 1)
            cell = json.loads(cells[0].read_text(encoding="utf-8"))
            self.assertEqual(cell["admission"]["status"], "ADMISSIBLE")
            trace_files = list((d / "traces").rglob("*.jsonl"))
            self.assertEqual(len(trace_files), 0, "raw trace must be removed after verified archival")
            archives = list((d / "traces").glob("*.zst"))
            self.assertEqual(len(archives), 1, "exactly one archive, no duplicates")
            self.assertEqual(cell["trace_count"]["telemetry"]["trace_sha256"],
                             cell["trace_count"]["telemetry"]["archive"]["sha256"])
            self.assertEqual(cell["trace_count"]["telemetry"]["archive"]["archive_sha256"],
                             hashlib.sha256(archives[0].read_bytes()).hexdigest())

    def test_output_digest_mismatch_inadmissible(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            launch = next(l for l in self.plan["launches"]
                          if l["config_id"] == "A-4GiB" and l["kind"] == "TIMING_COUNTED")
            runner_ok = self._Runner(self.plan, d, tokens=[1, 2, 3])
            tp = d / "traces" / "A-4GiB.jsonl"
            self._make_trace(tp, [1, 2, 3])
            A._default_run_one(self.launch, self.plan, d, runner_ok)
            runner_bad = self._Runner(self.plan, d, tokens=[9, 9, 9])
            res = A._default_run_one(launch, self.plan, d, runner_bad)
            self.assertFalse(A._default_pair(launch, res, self.plan, d))
            cells = list((d / "cells").glob("cell-A-4GiB-r*.json"))
            cell = json.loads(cells[0].read_text(encoding="utf-8"))
            self.assertEqual(cell["admission"]["status"], "INADMISSIBLE")
            self.assertTrue(cell["admission"]["failures"])

    def test_stderr_provenance_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            launch = next(l for l in self.plan["launches"]
                          if l["config_id"] == "A-4GiB" and l["kind"] == "TIMING_COUNTED")
            runner = self._Runner(self.plan, d, tokens=[1, 2, 3])
            res = A._default_run_one(launch, self.plan, d, runner)
            ev = res["evidence"]
            self.assertIn("stderr_sha256", ev)
            self.assertIn("stderr_bytes", ev)
            self.assertTrue(Path(ev["stderr_path"]).exists())

    def test_old_preflight_state_cannot_authorize_scientific(self):
        with tempfile.TemporaryDirectory() as d:
            st = A.State.load(Path(d) / "state.json")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1}
            st.save()
            with self.assertRaises(RuntimeError) as ctx:
                A.execute_stage(self.plan, PREREG, "scientific", st, out_dir=Path(d),
                                runner=None, sleep=lambda s: None,
                                free_ram_check=lambda b: None, run_one=lambda l: {"admitted": True},
                                pair_one=lambda l, r: True, storage_check=False)
            self.assertIn("provenance", str(ctx.exception))

    def test_no_model_process_started(self):
        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            with tempfile.TemporaryDirectory() as d:
                d = Path(d)
                launch = next(l for l in self.plan["launches"]
                              if l["config_id"] == "A-4GiB" and l["kind"] == "TIMING_COUNTED")
                A._default_run_one(launch, self.plan, d, self._Runner(self.plan, d, [1, 2, 3]))
        finally:
            A.subprocess.Popen = original


class TestStorageGateAndCompression(unittest.TestCase):
    ARTIFACT = REPO / "experiments" / "R3-02B-acquisition-driver" / "NTFS_COMPRESSION_TEST.json"

    def _compressed_dir(self, base):
        d = Path(base) / "compressed"
        d.mkdir(parents=True, exist_ok=True)
        import subprocess as sp
        sp.run(["compact.exe", "/c", "/s:" + str(d), "/q"], capture_output=True, text=True)
        return d

    def test_trace_dir_compression_detection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            d = self._compressed_dir(base)
            self.assertTrue(A.is_compressed(d))

    def test_storage_gate_fails_without_headroom(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            d = self._compressed_dir(base)
            failures = A.storage_gate(A.load_json(PHASE / "ACQUISITION_PLAN.json"), d,
                                      required_free=10 ** 15)
            self.assertTrue(failures)

    def test_storage_gate_passes_when_requirement_met(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            d = self._compressed_dir(base)
            failures = A.storage_gate(A.load_json(PHASE / "ACQUISITION_PLAN.json"), d,
                                      required_free=0)
            self.assertEqual(failures, [])

    def test_compressed_trace_logical_sha_unchanged(self):
        art = json.loads(self.ARTIFACT.read_text(encoding="utf-8"))
        self.assertTrue(art["sha_equal"])
        self.assertEqual(art["sha256_source"], art["sha256_copy"])
        src = Path(art["source"])
        if src.exists():
            self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(), art["sha256_source"])

    def test_compressed_trace_parser_output_unchanged(self):
        art = json.loads(self.ARTIFACT.read_text(encoding="utf-8"))
        self.assertTrue(art["counters_equal"])
        self.assertEqual(art["source_counters"], art["copy_counters"])


class TestLosslessArchive(unittest.TestCase):
    """Part J: lossless archive lifecycle regressions."""

    def setUp(self):
        self.plan = A.load_json(PHASE / "ACQUISITION_PLAN.json")
        self.cfg = next(c for c in self.plan["configurations"] if c["config_id"] == "A-4GiB")
        self.launch = next(l for l in self.plan["launches"]
                           if l["config_id"] == "A-4GiB" and l["kind"] == "TRACE_COUNT")

    def _raw(self, d, tokens=(1, 2, 3)):
        p = d / "traces" / "A-4GiB.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"step": 0, "policy": self.cfg["policy_id"], "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [1, 2, 3], "hit_experts": [], "miss_experts": [1, 2, 3],
             "protected_before": [], "resident_before": [], "evicted": [],
             "resident_after": [{"layer": 0, "expert": e} for e in (1, 2, 3)],
             "cache_resident_bytes_before": 0, "cache_resident_bytes_after": 300,
             "policy_resident_bytes_after": 300, "budget_bytes": self.cfg["budget_bytes"],
             "policy_params": self.cfg["params"]},
            {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 300},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return p

    class _Runner:
        def __init__(self, out, tokens):
            self.out, self.tokens = out, tokens

        def run_launch(self, launch, *, trace_path=None):
            log = self.out / "logs"
            log.mkdir(parents=True, exist_ok=True)
            e = log / f"{launch['seq']}.stderr.log"
            e.write_text("argine: ok\n")
            o = log / f"{launch['seq']}.stdout.log"
            o.write_text("ok\n")
            return {"tokens": list(self.tokens), "timings": {"prompt_ms": 500, "predicted_ms": 1000},
                    "wall_seconds": 1.0, "prompt_seconds": 0.5, "predicted_seconds": 1.0,
                    "exit_code": 0, "stdout_path": str(o), "stderr_path": str(e),
                    "process_cpu_seconds": 0.25}

    def test_roundtrip_sha_equal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            counters = A.T.frozen.extract_decision_trace(raw)
            info = A.archive_trace(raw, d / "traces" / "a.zst", counters)
            back = d / "back.jsonl"
            sha, size = A.decompress_and_hash(d / "traces" / "a.zst", back)
            self.assertEqual(sha, info["logical_sha256"])
            self.assertEqual(size, raw.stat().st_size)

    def test_parser_equal_after_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            counters = A.T.frozen.extract_decision_trace(raw)
            A.archive_trace(raw, d / "traces" / "a.zst", counters)
            back = d / "back.jsonl"
            A.decompress_and_hash(d / "traces" / "a.zst", back)
            self.assertEqual(A.T.frozen.extract_decision_trace(back), counters)

    def test_corrupted_archive_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            counters = A.T.frozen.extract_decision_trace(raw)
            info = A.archive_trace(raw, d / "traces" / "a.zst", counters)
            data = bytearray((d / "traces" / "a.zst").read_bytes())
            data[len(data) // 2] ^= 0xFF
            (d / "traces" / "a.zst").write_bytes(bytes(data))
            with self.assertRaises(A.LaunchFailure):
                A.verify_archive(d / "traces" / "a.zst", info["logical_sha256"],
                                 info["logical_bytes"], counters, d)

    def test_partial_archive_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            counters = A.T.frozen.extract_decision_trace(raw)
            info = A.archive_trace(raw, d / "traces" / "a.zst", counters)
            data = (d / "traces" / "a.zst").read_bytes()
            (d / "traces" / "a.zst").write_bytes(data[: max(1, len(data) // 3)])
            with self.assertRaises(A.LaunchFailure):
                A.verify_archive(d / "traces" / "a.zst", info["logical_sha256"],
                                 info["logical_bytes"], counters, d)

    def test_raw_retained_when_verification_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            counters = A.T.frozen.extract_decision_trace(raw)
            original = A.verify_archive
            A.verify_archive = lambda *a, **k: (_ for _ in ()).throw(A.LaunchFailure("bad"))
            try:
                with self.assertRaises(A.LaunchFailure):
                    A.archive_trace(raw, d / "traces" / "a.zst", counters)
            finally:
                A.verify_archive = original
            self.assertTrue(raw.exists())

    def test_raw_removed_only_after_verified_archive(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            A._default_run_one(self.launch, self.plan, d, self._Runner(d, [1, 2, 3]))
            self.assertFalse(raw.exists())
            self.assertTrue((d / "traces" / "A-4GiB.zst").exists())

    def test_resume_after_interrupted_compression(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = A.State.load(d / "state.json")
            st.mark(self.launch["seq"], "ARCHIVING", attempts=1)
            self.assertNotEqual(st.status(self.launch["seq"]), "SUCCEEDED")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                       "evidence": {"schema": 3, "request_completed_successfully": True, "server_return_code": 0, "intentional_shutdown": True, "shutdown_verified": True, "shutdown_mode": "terminate", "kill_used": False, "process_alive_before_shutdown": True, "stderr_error_markers": [], "admission": "ADMISSIBLE", "exit_code": 0,
                                                    "output_token_digest": "a" * 64,
                                                    "budget_invariant_ok": True}}
            called = []
            A.execute_stage(self.plan, PREREG, "scientific", st, out_dir=d, runner=None,
                            sleep=lambda s: None, free_ram_check=lambda b: None,
                            run_one=lambda l: (called.append(l["seq"]) or {"admitted": True}),
                            pair_one=lambda l, r: True, storage_check=False)
            self.assertIn(self.launch["seq"], called)

    def test_archive_and_logical_sha_recorded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            logical = hashlib.sha256(raw.read_bytes()).hexdigest()
            res = A._default_run_one(self.launch, self.plan, d, self._Runner(d, [1, 2, 3]))
            arch = d / "traces" / "A-4GiB.zst"
            self.assertEqual(res["evidence"]["archive"]["logical_sha256"], logical)
            self.assertEqual(res["evidence"]["archive"]["archive_sha256"],
                             hashlib.sha256(arch.read_bytes()).hexdigest())
            self.assertNotEqual(res["evidence"]["archive"]["archive_sha256"],
                                res["evidence"]["archive"]["logical_sha256"])

    def test_no_duplicate_raw_trace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._raw(d)
            A._default_run_one(self.launch, self.plan, d, self._Runner(d, [1, 2, 3]))
            self.assertEqual(list((d / "traces").glob("*.jsonl")), [])
            self.assertEqual(len(list((d / "traces").glob("*.zst"))), 1)

    def test_cell_references_logical_identity(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            raw = self._raw(d)
            logical = hashlib.sha256(raw.read_bytes()).hexdigest()
            A._default_run_one(self.launch, self.plan, d, self._Runner(d, [1, 2, 3]))
            timing = next(l for l in self.plan["launches"]
                          if l["config_id"] == "A-4GiB" and l["kind"] == "TIMING_COUNTED")
            res = A._default_run_one(timing, self.plan, d, self._Runner(d, [1, 2, 3]))
            self.assertTrue(A._default_pair(timing, res, self.plan, d))
            cell = json.loads(next((d / "cells").glob("cell-A-4GiB-r*.json")).read_text(encoding="utf-8"))
            self.assertEqual(cell["trace_count"]["telemetry"]["trace_sha256"], logical)

    def test_storage_requirement_includes_reserve(self):
        need = A.required_free_for_trace(self.plan, self.cfg, Path(tempfile.gettempdir()))
        self.assertGreaterEqual(need, int(A.SYSTEM_RESERVE_GIB * 1024 ** 3))

    def test_storage_gate_uses_conservative_peak(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = A.State.load(d / "state.json")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                       "evidence": {"schema": 3, "request_completed_successfully": True, "server_return_code": 0, "intentional_shutdown": True, "shutdown_verified": True, "shutdown_mode": "terminate", "kill_used": False, "process_alive_before_shutdown": True, "stderr_error_markers": [], "admission": "ADMISSIBLE", "exit_code": 0,
                                                    "output_token_digest": "a" * 64,
                                                    "budget_invariant_ok": True}}
            original_gate, original_free = A.storage_gate, A.free_bytes
            A.storage_gate = lambda *a, **k: []
            A.free_bytes = lambda p: 1
            try:
                with self.assertRaises(A.LaunchFailure):
                    A.execute_stage(self.plan, PREREG, "scientific", st,
                                    out_dir=d, runner=None, sleep=lambda s: None,
                                    free_ram_check=lambda b: None,
                                    run_one=lambda l: {"admitted": True},
                                    pair_one=lambda l, r: True, storage_check=True)
            finally:
                A.storage_gate, A.free_bytes = original_gate, original_free

    def test_current_free_space_detected(self):
        import shutil
        self.assertGreater(A.free_bytes(REPO), 0)
        self.assertEqual(A.free_bytes(REPO), int(shutil.disk_usage(str(REPO)).free))

    def test_no_process_started(self):
        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                self._raw(d)
                A._default_run_one(self.launch, self.plan, d, self._Runner(d, [1, 2, 3]))
        finally:
            A.subprocess.Popen = original



class TestPreflightAuthorization(unittest.TestCase):
    """Hotfix 5: exit-code / authorization consistency regressions."""

    def _ev(self, **over):
        ev = dict(CLEAN_EVIDENCE)
        ev.update(over)
        return ev

    def test_graceful_exit_zero_authorized(self):
        self.assertTrue(A.launch_clean(self._ev()))

    def test_true_server_failure_not_clean(self):
        ev = self._ev(server_return_code=1, exit_code=1, intentional_shutdown=False,
                      shutdown_mode="already_exited", shutdown_verified=False)
        self.assertFalse(A.launch_clean(ev))

    def test_intentional_shutdown_requires_full_semantics(self):
        # nonzero + intentional but kill escalated -> not clean
        self.assertFalse(A.launch_clean(self._ev(server_return_code=1, exit_code=1,
                                                 kill_used=True)))
        # nonzero + intentional, verified terminate -> clean
        self.assertTrue(A.launch_clean(self._ev(server_return_code=1, exit_code=1)))
        # nonzero + intentional but no verification -> not clean
        self.assertFalse(A.launch_clean(self._ev(server_return_code=1, exit_code=1,
                                                 shutdown_verified=False)))
        # arbitrary nonzero is never clean
        self.assertFalse(A.launch_clean(self._ev(server_return_code=2, exit_code=2)))

    def test_missing_digest_not_clean(self):
        self.assertFalse(A.launch_clean(self._ev(output_token_digest=None)))

    def test_budget_invariant_false_not_clean(self):
        self.assertFalse(A.launch_clean(self._ev(budget_invariant_ok=False)))

    def test_corrupt_stderr_provenance_not_clean(self):
        markers = A.stderr_error_markers(Path("does-not-exist.log"))
        self.assertTrue(markers)
        self.assertFalse(A.launch_clean(self._ev(stderr_error_markers=markers)))

    def test_runtime_error_marker_not_clean(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "e.log"
            p.write_text("ggml fatal error: aborting\n", encoding="utf-8")
            markers = A.stderr_error_markers(p)
            self.assertIn("fatal", markers)
            self.assertFalse(A.launch_clean(self._ev(stderr_error_markers=markers)))

    def test_old_schema_not_authorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = A.State.load(d / "state.json")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                       "evidence": {"schema": 2, "exit_code": 1,
                                                    "output_token_digest": "a" * 64,
                                                    "budget_invariant_ok": True,
                                                    "admission": "ADMISSIBLE"}}
            st.save()
            self.assertFalse(A.preflight_evidence_complete(A.State.load(d / "state.json")))

    def test_schema3_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = A.State.load(d / "state.json")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                       "evidence": dict(CLEAN_EVIDENCE)}
            st.save()
            self.assertTrue(A.preflight_evidence_complete(A.State.load(d / "state.json")))

    def test_stage_summary_implies_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            summary = A.execute_stage(A.load_json(PHASE / "ACQUISITION_PLAN.json"), PREREG,
                                      "preflight", A.State.load(d / "state.json"),
                                      out_dir=d, runner=None, sleep=lambda s: None,
                                      free_ram_check=lambda b: None,
                                      run_one=lambda l: {"admitted": True,
                                                         "evidence": dict(CLEAN_EVIDENCE)},
                                      storage_check=False)
            self.assertEqual(summary["stage_status"], "SUCCEEDED")
            reloaded = A.State.load(d / "state.json")
            self.assertTrue(A.preflight_evidence_complete(reloaded))

    def test_stage_summary_fails_when_evidence_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            bad = dict(CLEAN_EVIDENCE)
            bad["server_return_code"] = 3
            bad["exit_code"] = 3
            summary = A.execute_stage(A.load_json(PHASE / "ACQUISITION_PLAN.json"), PREREG,
                                      "preflight", A.State.load(d / "state.json"),
                                      out_dir=d, runner=None, sleep=lambda s: None,
                                      free_ram_check=lambda b: None,
                                      run_one=lambda l: {"admitted": True, "evidence": bad},
                                      storage_check=False)
            self.assertEqual(summary["stage_status"], "FAILED")
            self.assertFalse(A.preflight_evidence_complete(A.State.load(d / "state.json")))

    def test_wrong_out_dir_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc = A.main(["--plan", str(PHASE / "ACQUISITION_PLAN.json"), "--stage", "preflight",
                         "--out-dir", tmp, "--authorize-preflight", "--skip-file-hashes"])
            self.assertEqual(rc, 1)

class TestArchiveRecovery(unittest.TestCase):
    """R3-02D: verified recovery of interrupted TRACE_COUNT lifecycles."""

    def setUp(self):
        self.plan = A.load_json(PHASE / "ACQUISITION_PLAN.json")
        self.cfg = next(c for c in self.plan["configurations"] if c["config_id"] == "A-4GiB")
        self.launch = next(l for l in self.plan["launches"]
                           if l["config_id"] == "A-4GiB" and l["kind"] == "TRACE_COUNT")

    def _setup(self, d):
        d = Path(d)
        traces = d / "traces"
        traces.mkdir(parents=True, exist_ok=True)
        raw = traces / "A-4GiB.jsonl"
        rows = [
            {"step": 0, "policy": self.cfg["policy_id"], "layer": 0, "bytes_of_layer": 100,
             "requested_experts": [1, 2, 3], "hit_experts": [], "miss_experts": [1, 2, 3],
             "protected_before": [], "resident_before": [], "evicted": [],
             "resident_after": [{"layer": 0, "expert": e} for e in (1, 2, 3)],
             "cache_resident_bytes_before": 0, "cache_resident_bytes_after": 300,
             "policy_resident_bytes_after": 300, "budget_bytes": self.cfg["budget_bytes"],
             "policy_params": self.cfg["params"]},
            {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 300},
        ]
        raw.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        record = A.T.build_trace_count_record(
            trace_path=raw, identity=A.record_identity(self.plan, self.cfg),
            workload_id=self.plan["workload"]["id"], seed=42,
            budget_bytes=self.cfg["budget_bytes"], policy_id=self.cfg["policy_id"],
            policy_params=self.cfg["params"], output_tokens=[1, 2, 3])
        (d / "records").mkdir(exist_ok=True)
        (d / "records" / "trace_count-A-4GiB.json").write_text(
            json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        counters = A.T.frozen.extract_decision_trace(raw)
        A.compress_trace(raw, traces / "A-4GiB.zst")
        st = A.State.load(d / "state.json")
        st.mark(self.launch["seq"], "FAILED", attempts=2, error="[Errno 28] No space left on device")
        st.save()
        return d, raw, traces / "A-4GiB.zst", record

    def test_recover_offline(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            out = A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            self.assertEqual(len(out), 1)
            self.assertFalse(raw.exists())
            self.assertTrue((d / "admissions" / "trace_count-A-4GiB.json").exists())
            rec = A.load_json(d / "records" / "trace_count-A-4GiB.json")
            self.assertIn("archive_storage", rec["telemetry"])
            st = A.State.load(d / "state.json")
            self.assertEqual(st.status(self.launch["seq"]), "SUCCEEDED")

    def test_sha_mismatch_retains_raw(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            data = bytearray(arc.read_bytes())
            data[len(data) // 2] ^= 0xFF
            arc.write_bytes(bytes(data))
            with self.assertRaises(A.LaunchFailure):
                A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            self.assertTrue(raw.exists())
            self.assertEqual(A.State.load(d / "state.json").status(self.launch["seq"]), "FAILED")

    def test_byte_mismatch_retains_raw(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            rec = A.load_json(d / "records" / "trace_count-A-4GiB.json")
            rec["telemetry"]["archive"]["bytes"] += 1
            (d / "records" / "trace_count-A-4GiB.json").write_text(
                json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
            with self.assertRaises(A.LaunchFailure):
                A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            self.assertTrue(raw.exists())

    def test_counter_mismatch_retains_raw(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            rec = A.load_json(d / "records" / "trace_count-A-4GiB.json")
            rec["telemetry"]["counters"]["expert_miss_bytes"] += 1
            (d / "records" / "trace_count-A-4GiB.json").write_text(
                json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
            with self.assertRaises(A.LaunchFailure):
                A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            self.assertTrue(raw.exists())

    def test_truncated_archive_retains_raw(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            data = arc.read_bytes()
            arc.write_bytes(data[: max(1, len(data) // 2)])
            with self.assertRaises(Exception):
                A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            self.assertTrue(raw.exists())

    def test_admission_created_canonically(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            rec = A.load_json(d / "records" / "trace_count-A-4GiB.json")
            adm = A.load_json(d / "admissions" / "trace_count-A-4GiB.json")
            self.assertEqual(adm, A.T.admit_record(rec))
            self.assertEqual(adm["status"], "ADMISSIBLE")

    def test_raw_deleted_only_after_persistence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            original = A.write_json_atomic
            def boom(path, obj):
                if str(path).endswith("admissions\\trace_count-A-4GiB.json"):
                    raise OSError("simulated persistence failure")
                return original(path, obj)
            A.write_json_atomic = boom
            try:
                with self.assertRaises(OSError):
                    A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            finally:
                A.write_json_atomic = original
            self.assertTrue(raw.exists())

    def test_recovery_runs_before_storage_gate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            order = []
            original_recover, original_gate = A.recover_interrupted, A.storage_gate
            A.recover_interrupted = lambda *a, **k: (order.append("recover") or [])
            A.storage_gate = lambda *a, **k: (order.append("gate") or [])
            st = A.State.load(d / "state.json")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                       "evidence": dict(CLEAN_EVIDENCE)}
            try:
                A.execute_stage(self.plan, PREREG, "scientific", st, out_dir=d, runner=None,
                                sleep=lambda s: None, free_ram_check=lambda b: None,
                                run_one=lambda l: {"admitted": True, "evidence": dict(CLEAN_EVIDENCE)},
                                pair_one=lambda l, r: True, storage_check=True)
            finally:
                A.recover_interrupted, A.storage_gate = original_recover, original_gate
            self.assertEqual(order[:2], ["recover", "gate"])

    def test_recovered_launch_not_rerun(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            st = A.State.load(d / "state.json")
            st.stage_status["preflight"] = "SUCCEEDED"
            for k in (1, 2, 3):
                st.launches[str(k)] = {"status": "SUCCEEDED", "attempts": 1,
                                       "evidence": dict(CLEAN_EVIDENCE)}
            called = []
            A.execute_stage(self.plan, PREREG, "scientific", st, out_dir=d, runner=None,
                            sleep=lambda s: None, free_ram_check=lambda b: None,
                            run_one=lambda l: (called.append(l["seq"]) or
                                               {"admitted": True, "evidence": dict(CLEAN_EVIDENCE)}),
                            pair_one=lambda l, r: True, storage_check=False)
            self.assertNotIn(self.launch["seq"], called)

    def test_failed_attempt_provenance_preserved(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            rec = A.State.load(d / "state.json").launches[str(self.launch["seq"])]
            self.assertEqual(rec["attempts"], 2)
            self.assertEqual(rec["error"], "[Errno 28] No space left on device")
            self.assertEqual(rec["recovery"]["original_attempts"], 2)
            self.assertEqual(rec["recovery"]["recovered_from_status"], "FAILED")

    def test_ratio_constant_updated(self):
        self.assertAlmostEqual(A.ARCHIVE_RATIO_MEASURED, 0.05062, places=5)
        need = A.required_free_for_trace(self.plan, self.cfg, Path(tempfile.gettempdir()))
        gib = 1024 ** 3
        expected = int(A.SYSTEM_RESERVE_GIB * gib + 14.39 * 1.10 * gib + 14.39 * 1.10 * gib * 0.05062)
        self.assertEqual(need, expected)

    def test_cells_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            cells = d / "cells"
            cells.mkdir()
            cell = cells / "cell-A-4GiB-r1.json"
            cell.write_text('{"artifact": "R3-01-CELL"}', encoding="utf-8")
            before = hashlib.sha256(cell.read_bytes()).hexdigest()
            A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            self.assertEqual(hashlib.sha256(cell.read_bytes()).hexdigest(), before)

    def test_resume_plan_excludes_completed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d, raw, arc, record = self._setup(tmp)
            A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
            st = A.State.load(d / "state.json")
            rem = A.remaining_launches(self.plan, st)
            self.assertNotIn(self.launch["seq"], [l["seq"] for l in rem])
            self.assertEqual(len(rem), 52 - 1 - 0)  # only this synthetic launch is SUCCEEDED

    def test_no_process_started(self):
        import tempfile
        original = A.subprocess.Popen
        A.subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                d, raw, arc, record = self._setup(tmp)
                A.recover_interrupted(d, self.plan, A.State.load(d / "state.json"))
        finally:
            A.subprocess.Popen = original


class TestStorageFailureSemantics(unittest.TestCase):
    def test_storage_failure_not_retried(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            st = A.State.load(d / "state.json")
            summary = A.execute_stage(A.load_json(PHASE / "ACQUISITION_PLAN.json"), PREREG,
                                      "preflight", st, out_dir=d, runner=None,
                                      sleep=lambda s: None, free_ram_check=lambda b: None,
                                      run_one=lambda l: (_ for _ in ()).throw(A.StorageFailure("ENOSPC")),
                                      storage_check=False)
            self.assertEqual(summary["stage_status"], "FAILED")
            self.assertEqual(st.launches["1"]["attempts"], 1)

    def test_oserror_maps_to_storage_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            plan = A.load_json(PHASE / "ACQUISITION_PLAN.json")
            cfg = next(c for c in plan["configurations"] if c["config_id"] == "A-4GiB")
            launch = next(l for l in plan["launches"]
                          if l["config_id"] == "A-4GiB" and l["kind"] == "TRACE_COUNT")
            rows = [
                {"step": 0, "policy": cfg["policy_id"], "layer": 0, "bytes_of_layer": 100,
                 "requested_experts": [1, 2, 3], "hit_experts": [], "miss_experts": [1, 2, 3],
                 "protected_before": [], "resident_before": [], "evicted": [],
                 "resident_after": [{"layer": 0, "expert": 1}],
                 "cache_resident_bytes_before": 0, "cache_resident_bytes_after": 300,
                 "policy_resident_bytes_after": 300, "budget_bytes": cfg["budget_bytes"],
                 "policy_params": cfg["params"]},
                {"step": 0, "complete": 1, "cache_resident_bytes_post_service": 300},
            ]
            class R:
                def run_launch(self, launch, *, trace_path=None):
                    trace_path.parent.mkdir(parents=True, exist_ok=True)
                    trace_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
                    (d / "o").write_text("ok"); (d / "e").write_text("ok")
                    return {"tokens": [1], "timings": {}, "wall_seconds": 1.0,
                            "prompt_seconds": 0.1, "predicted_seconds": 0.2,
                            "exit_code": 0, "stdout_path": str(d / "o"), "stderr_path": str(d / "e"),
                            "process_cpu_seconds": 0.1}
            original = A.archive_trace
            A.archive_trace = lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left"))
            try:
                with self.assertRaises(A.StorageFailure):
                    A._default_run_one(launch, plan, d, R())
            finally:
                A.archive_trace = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
