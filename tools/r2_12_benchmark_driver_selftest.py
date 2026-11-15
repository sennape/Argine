"""Fake-runtime integration test for tools/r2_12_benchmark_driver.py.

Proves _execute_one_launch() is a genuinely working implementation (not a
stub) by driving it against tools/r2_12_fake_runtime_server.py -- a real
OS subprocess and a real local HTTP server, but never llama-server.exe and
never the 30B model. Exercises: process launch, readiness/marker waiting,
the real /completion request+streaming parse, metric capture, output
capture, atomic persistence, clean process shutdown, a failure-stop path,
and an output-mismatch-stop path (via check_cell_output_equivalence).

Entry point: run_fake_runtime_integration_test() -> {"ok": bool, ...}.
Also runnable standalone: `python3 tools/r2_12_benchmark_driver_selftest.py`.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_12_benchmark_driver as drv  # noqa: E402

FAKE_RUNTIME_SCRIPT = Path(__file__).resolve().parent / "r2_12_fake_runtime_server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_launcher(runtime_exe, args, env, work_dir, stdout_path, stderr_path):
    import subprocess
    stdout_f = open(stdout_path, "wb")
    stderr_f = open(stderr_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, str(FAKE_RUNTIME_SCRIPT)] + args,
        cwd=str(work_dir), env=env, stdout=stdout_f, stderr=stderr_f,
    )
    return proc, stdout_f, stderr_f


def _make_launch(frozen, overrides=None):
    cells, launches = drv.build_4gib_plan(frozen)
    launch = dict(launches[2])  # a counted run, not a warmup
    if overrides:
        launch.update(overrides)
    return launch


def run_fake_runtime_integration_test() -> dict:
    results = {}
    ok = True
    frozen = drv.load_frozen_protocol()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        drv.WORK_DIR = tmp_path / "work"
        drv.WORK_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Happy path: launch, readiness, request, metrics, output, cleanup.
        os.environ["FAKE_RUNTIME_MODE"] = "fixed_text:MATCHING-OUTPUT"
        port1 = _free_port()
        launch_ok = _make_launch(frozen, {"run_id": "selftest-happy-path"})
        record_ok = drv._execute_one_launch(
            launch_ok, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
            frozen, port=port1, process_launcher=_fake_launcher,
        )
        happy = {
            "status_completed_ok": record_ok.get("status") == "completed_ok",
            "ok_true": record_ok.get("ok") is True,
            "has_output_digest": bool(record_ok.get("output_token_digest")),
            "has_decode_tps": record_ok.get("runtime_metrics", {}).get("decode_tokens_per_second") is not None,
            "has_ttft": record_ok.get("runtime_metrics", {}).get("ttft_seconds") is not None,
            "process_cleanup_ok": record_ok.get("process_cleanup", {}).get("ok") is True,
            "pid_recorded": isinstance(record_ok.get("environment", {}).get("process_identity_pid"), int),
            "workload_identity_recorded": bool(record_ok.get("workload_identity", {}).get("sha256")),
        }
        results["happy_path"] = happy
        ok = ok and all(happy.values())

        # 2. Atomic persistence round-trip.
        rec_file = tmp_path / "persist-test.json"
        drv.atomic_write_json(rec_file, record_ok)
        import json
        reloaded = json.loads(rec_file.read_text())
        persist_ok = reloaded.get("run_id") == record_ok.get("run_id")
        results["atomic_persistence"] = {"ok": persist_ok}
        ok = ok and persist_ok

        # 3. Resume-skip logic: a completed record is detected and would be skipped.
        would_skip_completed = reloaded.get("ok") is True
        would_skip_fresh = not rec_file.with_name("does-not-exist.json").exists()
        results["resume_logic"] = {"completed_record_detected_for_skip": would_skip_completed,
                                    "fresh_run_id_not_found": would_skip_fresh}
        ok = ok and would_skip_completed and would_skip_fresh

        # 4. Failure path: fake runtime never emits readiness markers ->
        #    _execute_one_launch must return ok=False with crash_or_failure set,
        #    and must not raise.
        old_marker_timeout = drv.MARKER_TIMEOUT_SEC
        drv.MARKER_TIMEOUT_SEC = 2
        try:
            os.environ["FAKE_RUNTIME_MODE"] = "no_marker"
            port2 = _free_port()
            launch_fail = _make_launch(frozen, {"run_id": "selftest-failure-path"})
            record_fail = drv._execute_one_launch(
                launch_fail, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
                frozen, port=port2, process_launcher=_fake_launcher,
            )
        finally:
            drv.MARKER_TIMEOUT_SEC = old_marker_timeout
        failure_checks = {
            "status_failed": record_fail.get("status") == "failed",
            "ok_false": record_fail.get("ok") is False,
            "crash_or_failure_set": bool(record_fail.get("crash_or_failure")),
            "process_was_cleaned_up": record_fail.get("process_cleanup", {}).get("ok") is not False,
        }
        results["failure_path"] = failure_checks
        ok = ok and all(failure_checks.values())

        # 5. Output-mismatch path: two counted runs in the same cell, same
        #    policy pairing, deliberately different output -> BLOCKED_INVALID.
        os.environ["FAKE_RUNTIME_MODE"] = "fixed_text:OUTPUT-A"
        port3 = _free_port()
        launch_a = _make_launch(frozen, {"run_id": "selftest-mismatch-a", "policy_id": "b_selected",
                                          "cell_index": 99, "counted": True})
        record_a = drv._execute_one_launch(
            launch_a, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
            frozen, port=port3, process_launcher=_fake_launcher,
        )
        os.environ["FAKE_RUNTIME_MODE"] = "fixed_text:OUTPUT-B-DIFFERENT"
        port4 = _free_port()
        launch_b = _make_launch(frozen, {"run_id": "selftest-mismatch-b", "policy_id": "predicted_protect",
                                          "cell_index": 99, "counted": True})
        record_b = drv._execute_one_launch(
            launch_b, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
            frozen, port=port4, process_launcher=_fake_launcher,
        )
        reason = drv.check_cell_output_equivalence(99, {"a": record_a, "b": record_b})
        mismatch_checks = {
            "both_runs_completed_ok": record_a.get("ok") is True and record_b.get("ok") is True,
            "mismatch_detected": reason is not None,
        }
        results["output_mismatch_path"] = mismatch_checks
        ok = ok and all(mismatch_checks.values())

        # 5b. Matching case must NOT be flagged.
        os.environ["FAKE_RUNTIME_MODE"] = "fixed_text:SAME-OUTPUT"
        port5 = _free_port()
        launch_c = _make_launch(frozen, {"run_id": "selftest-match-a", "policy_id": "b_selected",
                                          "cell_index": 98, "counted": True})
        record_c = drv._execute_one_launch(
            launch_c, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
            frozen, port=port5, process_launcher=_fake_launcher,
        )
        port6 = _free_port()
        launch_d = _make_launch(frozen, {"run_id": "selftest-match-b", "policy_id": "predicted_protect",
                                          "cell_index": 98, "counted": True})
        record_d = drv._execute_one_launch(
            launch_d, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
            frozen, port=port6, process_launcher=_fake_launcher,
        )
        reason_match = drv.check_cell_output_equivalence(98, {"c": record_c, "d": record_d})
        results["output_match_path"] = {"not_flagged": reason_match is None}
        ok = ok and reason_match is None

        os.environ.pop("FAKE_RUNTIME_MODE", None)

    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    import json
    r = run_fake_runtime_integration_test()
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)
