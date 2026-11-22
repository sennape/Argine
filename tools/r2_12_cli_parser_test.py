"""CLI-parser-level regression tests for the R2-12 benchmark driver's
--budget-gib argument (preflight/execute/diagnose), covering the
diagnose-subparser regression reported 2026-11-15 (diagnose had no
--budget-gib at all, and cmd_diagnose itself hardcoded budget_gib=4.0
regardless of any argument).

These tests invoke the ACTUAL argparse parser via
r2_12_benchmark_driver.build_arg_parser() -- the same object main() uses
-- rather than calling cmd_diagnose()/cmd_preflight()/cmd_execute()
directly, so a parser-only regression (an argument silently missing from
one subparser) is caught even if the underlying command function is
already correct.

Zero model inference anywhere in this file: the only "launch" exercised
is tools/r2_12_fake_runtime_server.py, a local HTTP stand-in, invoked via
the pre-existing fake-runtime integration pattern.

Entry point: run_cli_parser_tests() -> {"ok": bool, "sub_results": {...}}.
Also runnable standalone: `python3 tools/r2_12_cli_parser_test.py`.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_12_benchmark_driver as drv  # noqa: E402

EXPECTED_BASELINE_PARAMS_BY_BUDGET = {
    1.5: (0.999, 1),
    4.0: (0.99, 64),
    8.0: (0.999, 16),
}


def _check_diagnose_accepts_each_frozen_budget(parser, results):
    ok = True
    for bgib in (1.5, 4.0, 8.0):
        ns = parser.parse_args(["diagnose", "--budget-gib", str(bgib)])
        this_ok = ns.budget_gib == bgib
        results[f"diagnose_parser_accepts_{bgib}gib"] = {"ok": this_ok, "parsed": ns.budget_gib}
        ok = ok and this_ok
    return ok


def _check_diagnose_rejects_unsupported_budget(parser, results):
    import contextlib
    import io

    rejected = False
    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_buf):
            parser.parse_args(["diagnose", "--budget-gib", "2.0"])
    except SystemExit:
        rejected = True
    results["diagnose_parser_rejects_unsupported_budget"] = {
        "ok": rejected, "stderr": stderr_buf.getvalue().strip(),
    }
    return rejected


def _check_diagnose_default_budget(parser, results):
    ns = parser.parse_args(["diagnose"])
    this_ok = ns.budget_gib == 4.0
    results["diagnose_parser_omitted_budget_defaults_to_4gib"] = {"ok": this_ok, "parsed": ns.budget_gib}
    return this_ok


def _check_preflight_execute_still_accept_all_budgets(parser, results):
    ok = True
    for sub in ("preflight", "execute"):
        for bgib in (1.5, 4.0, 8.0):
            ns = parser.parse_args([sub, "--budget-gib", str(bgib)])
            this_ok = ns.budget_gib == bgib
            results[f"{sub}_parser_still_accepts_{bgib}gib"] = this_ok
            ok = ok and this_ok
    return ok


def _check_selected_budget_reaches_launch_config(results):
    """End-to-end (item 4/5): the budget selected on the diagnose CLI must
    reach build_budget_plan's own per-budget params via the SAME helpers
    (budget_gib_to_bytes / resolve_baseline_params_for_budget /
    resolve_predictor_decay_factor), and the ACTUAL child env produced by
    a real (fake-runtime) cmd_diagnose invocation must carry those exact
    values -- not just the resolver functions in isolation."""
    from r2_12_benchmark_driver_selftest import _fake_launcher, _free_port

    parser = drv.build_arg_parser()
    frozen = drv.load_frozen_protocol()
    ok = True

    for bgib, (expected_decay, expected_interval) in EXPECTED_BASELINE_PARAMS_BY_BUDGET.items():
        # Resolver-level check (no process launch at all).
        bb = drv.budget_gib_to_bytes(bgib)
        params = drv.resolve_baseline_params_for_budget(frozen, bb)
        resolver_ok = (params["decay_factor"] == expected_decay
                       and params["aging_interval_steps"] == expected_interval)
        results[f"resolver_params_{bgib}gib"] = {
            "ok": resolver_ok, "decay_factor": params["decay_factor"],
            "aging_interval_steps": params["aging_interval_steps"],
        }
        ok = ok and resolver_ok

        # End-to-end CLI -> cmd_diagnose -> _execute_one_launch -> fake
        # runtime (zero real inference) -> actual child env in the record.
        ns = parser.parse_args(["diagnose", "--budget-gib", str(bgib),
                                 "--runtime", "fake-runtime-exe", "--model", "fake-model",
                                 "--store", "fake-store"])
        with tempfile.TemporaryDirectory() as tmp:
            old_work_dir = drv.WORK_DIR
            old_launcher = drv.default_process_launcher
            drv.WORK_DIR = Path(tmp)
            drv.default_process_launcher = _fake_launcher
            try:
                ns.port = _free_port()
                import io
                import contextlib
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = ns.func(ns)
                breakdown = json.loads(buf.getvalue())
            finally:
                drv.default_process_launcher = old_launcher
                drv.WORK_DIR = old_work_dir

        e2e_ok = (
            rc == 0
            and breakdown.get("budget_gib") == bgib
            and breakdown.get("budget_bytes") == bb
            and breakdown.get("actual_child_env_retention_decay") == str(expected_decay)
            and breakdown.get("actual_child_env_retention_interval") == str(expected_interval)
            and breakdown.get("actual_child_env_expert_budget") == str(bb)
            and breakdown.get("resolved_policy_params", {}).get("baseline_decay_factor") == expected_decay
            and breakdown.get("resolved_policy_params", {}).get("baseline_aging_interval_steps") == expected_interval
            and bgib_label_in_output_path(breakdown.get("output_path", ""), bgib)
        )
        results[f"diagnose_end_to_end_child_env_{bgib}gib"] = {
            "ok": e2e_ok, "returncode": rc, "breakdown": breakdown,
        }
        ok = ok and e2e_ok

    return ok


def bgib_label_in_output_path(path: str, bgib: float) -> bool:
    label = drv.BUDGET_FILENAME_LABELS[bgib]
    return label in path


def run_cli_parser_tests() -> dict:
    results: dict = {}
    ok = True

    parser = drv.build_arg_parser()

    ok = ok and _check_diagnose_accepts_each_frozen_budget(parser, results)
    ok = ok and _check_diagnose_rejects_unsupported_budget(parser, results)
    ok = ok and _check_diagnose_default_budget(parser, results)
    ok = ok and _check_preflight_execute_still_accept_all_budgets(parser, results)

    try:
        ok_e2e = _check_selected_budget_reaches_launch_config(results)
    except Exception as e:
        results["diagnose_end_to_end_child_env"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        ok_e2e = False
    ok = ok and ok_e2e

    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    print(json.dumps(run_cli_parser_tests(), indent=2, default=str))
