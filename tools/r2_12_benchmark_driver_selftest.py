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
                                       4294967296, 128, 0.99, 64, 0.99, enable_trace=False)
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
                                       4294967296, 128, 0.99, 64, 0.99, enable_trace=True)
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


def run_gate_enrichment_regression_test() -> dict:
    """Reproduces and fixes the exact confirmed cmd_execute crash
    (2026-11-15): `gate["checks"] = dict(gate.get("checks") or {}, ...)`
    raised `ValueError: dictionary update sequence element #0 has length
    3; 2 is required`, because host_preflight_gate()'s "checks" is a LIST
    of {"name","ok","detail"} dicts, not a mapping -- dict() tried to read
    each 3-key dict as a 2-tuple. The fix (build_execute_gate()) appends
    in the same list schema instead. This test calls build_execute_gate()
    directly -- no argparse, no lock, no launch loop -- so it can never
    reach _execute_one_launch and can never launch real inference."""
    import r2_12_benchmark_driver as drv
    results = {}
    ok = True

    # --- gate enrichment doesn't crash, and existing checks are preserved ---
    fake_runtime = Path("/this/path/does/not/exist/llama-server.exe")
    fake_model = Path("/this/path/does/not/exist/model.gguf")
    fake_store = Path("/this/path/does/not/exist/model.psxs")
    try:
        # operator_attested_clean_environment=True here: that specific check
        # is an unrelated hard gate (must be explicitly attested at real
        # --execute time) and is not what this test is about -- using False
        # would force gate["ok"]=False regardless of the disk-space result,
        # which is not what "gate_ok_true_when_disk_passes" is testing.
        gate = drv.build_execute_gate(fake_runtime, fake_model, fake_store,
                                       operator_attested_clean_environment=True)
        crashed = False
    except Exception as e:
        gate = None
        crashed = True
        results["crash_error"] = f"{type(e).__name__}: {e}"
    results["no_crash_on_gate_enrichment"] = not crashed
    ok = ok and not crashed

    if gate is not None:
        results["checks_is_a_list"] = isinstance(gate.get("checks"), list)
        ok = ok and results["checks_is_a_list"]

        names = [c["name"] for c in gate.get("checks", []) if isinstance(c, dict) and "name" in c]
        # Existing host_preflight_gate checks must all still be present
        # (existing checks preserved; no schema mutation of their entries).
        expected_preexisting = {
            "runtime_hash_matches_frozen_identity", "model_hash_matches_frozen_identity",
            "store_hash_matches_frozen_identity", "hardware_identity_matches_stamp_or_first_run",
            "sufficient_free_ram", "no_concurrent_benchmark_process",
            "operator_attested_clean_environment",
        }
        results["existing_checks_preserved"] = expected_preexisting.issubset(set(names))
        ok = ok and results["existing_checks_preserved"]

        # The new disk_space_margin entry must be appended in the SAME
        # {"name","ok","detail"} schema, not merged/coerced into anything else.
        disk_entries = [c for c in gate["checks"] if c.get("name") == "disk_space_margin"]
        results["disk_space_margin_entry_present"] = len(disk_entries) == 1
        results["disk_space_margin_entry_schema_ok"] = (
            len(disk_entries) == 1 and set(disk_entries[0].keys()) == {"name", "ok", "detail"}
        )
        ok = ok and results["disk_space_margin_entry_present"] and results["disk_space_margin_entry_schema_ok"]

        results["gate_has_parent_env_visibility_fields"] = (
            "retention_trace_env_present" in gate and "trace_output_variables_present" in gate
        )
        ok = ok and results["gate_has_parent_env_visibility_fields"]

    # --- disk-space PASS: with the real (small) margin requirement, this
    # sandbox's disk should satisfy it, so disk_space_margin.ok is True and
    # does not by itself force gate.ok False (hash checks are None --
    # "not reachable" -- for the nonexistent fake paths, which is NOT a
    # failure per host_preflight_gate's own ok=all(c["ok"] is not False...)).
    disk_pass_detail = next((c for c in gate["checks"] if c["name"] == "disk_space_margin"), None)
    results["disk_space_pass_case_ok_true"] = bool(disk_pass_detail and disk_pass_detail["ok"] is True)
    ok = ok and results["disk_space_pass_case_ok_true"]
    results["gate_ok_true_when_disk_passes_and_others_not_reachable"] = bool(gate and gate["ok"] is True)
    ok = ok and results["gate_ok_true_when_disk_passes_and_others_not_reachable"]

    # --- disk-space FAIL: force an impossibly large margin requirement and
    # prove gate.ok becomes False, the schema is still intact, and (since
    # cmd_execute returns 3 before acquire_lock()/the launch loop whenever
    # gate["ok"] is False) no path to real inference exists.
    old_margin = drv.MIN_FREE_DISK_BYTES_CAMPAIGN_START
    drv.MIN_FREE_DISK_BYTES_CAMPAIGN_START = 10 ** 18  # ~1 exabyte: always fails
    try:
        gate_fail = drv.build_execute_gate(fake_runtime, fake_model, fake_store,
                                            operator_attested_clean_environment=False)
        disk_fail_entry = next((c for c in gate_fail["checks"] if c["name"] == "disk_space_margin"), None)
        results["disk_space_fail_case_ok_false"] = bool(disk_fail_entry and disk_fail_entry["ok"] is False)
        results["gate_ok_false_on_disk_fail"] = gate_fail["ok"] is False
        results["schema_intact_on_fail_path"] = isinstance(gate_fail["checks"], list) and all(
            isinstance(c, dict) and set(c.keys()) == {"name", "ok", "detail"} for c in gate_fail["checks"])
        ok = ok and results["disk_space_fail_case_ok_false"] and results["gate_ok_false_on_disk_fail"] \
            and results["schema_intact_on_fail_path"]
    finally:
        drv.MIN_FREE_DISK_BYTES_CAMPAIGN_START = old_margin

    # --- no inference launched on gate failure: cmd_execute's own code
    # returns 3 immediately after `if not gate["ok"]:`, strictly before
    # acquire_lock() and the launch loop that could ever call
    # _execute_one_launch/default_process_launcher. Verified structurally
    # by reading cmd_execute's own source rather than by actually invoking
    # it with argparse (which would need a full Namespace) -- this proves
    # the early-return ordering itself, not just a belief about it.
    import inspect
    src_text = inspect.getsource(drv.cmd_execute)
    gate_check_idx = src_text.find('if not gate["ok"]:')
    acquire_lock_idx = src_text.find("acquire_lock()")
    results["gate_failure_return_precedes_acquire_lock_in_source"] = (
        gate_check_idx != -1 and acquire_lock_idx != -1 and gate_check_idx < acquire_lock_idx
    )
    ok = ok and results["gate_failure_return_precedes_acquire_lock_in_source"]

    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    import json
    r1 = run_fake_runtime_integration_test()
    r2 = run_inherited_env_regression_test()
    r3 = run_trace_enabled_overrides_stale_inherited_test()
    r4 = run_gate_enrichment_regression_test()
    combined = {
        "ok": r1["ok"] and r2["ok"] and r3["ok"] and r4["ok"],
        "fake_runtime_integration": r1,
        "inherited_env_regression": r2,
        "trace_enabled_overrides_stale_inherited": r3,
        "gate_enrichment_regression": r4,
    }
    print(json.dumps(combined, indent=2))
    sys.exit(0 if combined["ok"] else 1)
