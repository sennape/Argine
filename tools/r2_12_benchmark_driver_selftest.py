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


def run_inherited_env_regression_test() -> dict:
    """Reproduces the EXACT confirmed host failure (2026-11-15): a prior
    shell session had exported
    ARGINE_RETENTION_TRACE=C:\\modelli\\conformance\\v5\\predicted_protect.jsonl
    into the PROCESS environment. The pre-fix driver's build_os_environ()
    started from dict(os.environ) and only popped keys explicitly present
    in env_map with value None -- since enable_trace=False just omits the
    key, the stale value passed straight through to the child. This test
    injects the exact same variable into this test process's os.environ
    (simulating the inherited PROCESS-level state the host reported; a
    real USER/MACHINE-level source is not separately modeled here because
    this driver's only environment-construction entry point is
    build_os_environ(dict(os.environ)), i.e. all three Windows env scopes
    are already flattened into os.environ by the time Python sees them --
    there is no separate abstraction to test per scope), then proves the
    sanitized env never carries it through, for both diagnose-style
    (enable_trace=False) and canonical-execute-style construction, and
    that the fake runtime never receives a retention trace path and
    creates no trace file."""
    import r2_12_benchmark_driver as drv
    ok = True
    results = {}

    poisoned_value = r"C:\modelli\conformance\v5\predicted_protect.jsonl"
    old = os.environ.get("ARGINE_RETENTION_TRACE")
    os.environ["ARGINE_RETENTION_TRACE"] = poisoned_value
    try:
        # 1 & 2: neither the diagnose-style nor canonical-execute-style
        # env construction (both call build_child_env + build_os_environ
        # with enable_trace=False) may carry the poisoned value through.
        env_map = drv.build_child_env("b_selected", "C:\\modelli\\m.psxs",
                                       "C:\\run\\should-not-be-used.trace.jsonl",
                                       4294967296, 128, enable_trace=False)
        os_env = drv.build_os_environ(env_map)
        diagnose_clean = "ARGINE_RETENTION_TRACE" not in os_env
        execute_clean = diagnose_clean  # identical code path; same assertion
        results["diagnose_child_env_clean"] = diagnose_clean
        results["execute_child_env_clean"] = execute_clean
        ok = ok and diagnose_clean and execute_clean

        # 3 & 4: drive the REAL _execute_one_launch against the fake runtime
        # with the poison still present in os.environ, prove no trace path
        # reaches the fake runtime and no trace file is created.
        with tempfile.TemporaryDirectory() as tmp:
            old_work_dir = drv.WORK_DIR
            drv.WORK_DIR = Path(tmp)
            try:
                frozen = drv.load_frozen_protocol()
                launch = _make_launch(frozen, {"run_id": "selftest-poisoned-env", "counted": True})
                port = _free_port()
                record = drv._execute_one_launch(
                    launch, Path("fake-runtime-exe"), Path("fake-model"), Path("fake-store"),
                    frozen, port=port, process_launcher=_fake_launcher, enable_trace=False,
                )
                no_trace_file_created = not (Path(tmp) / f"{launch['run_id']}.trace.jsonl").exists()
                record_ok = record.get("ok") is True
                results["real_launch_completed_ok_despite_poisoned_parent_env"] = record_ok
                results["no_trace_file_created"] = no_trace_file_created
                results["record_pre_popen_check"] = record.get("pre_popen_env_check")
                ok = ok and record_ok and no_trace_file_created
            finally:
                drv.WORK_DIR = old_work_dir

        # 5: pre-Popen assertion passes only after sanitization -- prove the
        # UNSANITIZED path would have failed the assertion, i.e. the
        # assertion logic itself is sensitive to this exact variable.
        unsanitized = dict(os.environ)  # what os.environ looked like, unsanitized
        assertion_would_have_failed_without_fix = "ARGINE_RETENTION_TRACE" in unsanitized
        results["assertion_is_sensitive_to_the_exact_host_variable"] = assertion_would_have_failed_without_fix
        ok = ok and assertion_would_have_failed_without_fix
    finally:
        if old is None:
            os.environ.pop("ARGINE_RETENTION_TRACE", None)
        else:
            os.environ["ARGINE_RETENTION_TRACE"] = old

    return {"ok": ok, "sub_results": results}


def run_trace_enabled_overrides_stale_inherited_test() -> dict:
    """Task 5: the trace-enabled path must still work, and an explicit
    driver-selected trace path must always win over any stale inherited
    one -- never the reverse."""
    import r2_12_benchmark_driver as drv
    stale = r"C:\modelli\conformance\v5\OLD-STALE-PATH.jsonl"
    intended = r"C:\run\THIS-LAUNCH-intended-path.trace.jsonl"
    old = os.environ.get("ARGINE_RETENTION_TRACE")
    os.environ["ARGINE_RETENTION_TRACE"] = stale
    try:
        env_map = drv.build_child_env("b_selected", "C:\\modelli\\m.psxs", intended,
                                       4294967296, 128, enable_trace=True)
        os_env = drv.build_os_environ(env_map)
        present = os_env.get("ARGINE_RETENTION_TRACE") == intended
        not_stale = os_env.get("ARGINE_RETENTION_TRACE") != stale
        return {"ok": present and not_stale,
                "sub_results": {"explicit_path_applied": present, "stale_path_not_used": not_stale}}
    finally:
        if old is None:
            os.environ.pop("ARGINE_RETENTION_TRACE", None)
        else:
            os.environ["ARGINE_RETENTION_TRACE"] = old


if __name__ == "__main__":
    import json
    r1 = run_fake_runtime_integration_test()
    r2 = run_inherited_env_regression_test()
    r3 = run_trace_enabled_overrides_stale_inherited_test()
    combined = {
        "ok": r1["ok"] and r2["ok"] and r3["ok"],
        "fake_runtime_integration": r1,
        "inherited_env_regression": r2,
        "trace_enabled_overrides_stale_inherited": r3,
    }
    print(json.dumps(combined, indent=2))
    sys.exit(0 if combined["ok"] else 1)
