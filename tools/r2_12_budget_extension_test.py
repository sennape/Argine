"""Regression tests for the budget-aware R2-12 driver extension (R2-12
Phase F-prime: generalizing the 4 GiB-only driver to 1.5/4/8 GiB).

Zero model inference. Proves, against the real driver code (not
duplicated test-only logic):
  1.  existing 4 GiB plan is unchanged
  2.  1.5 GiB plan matches the frozen schedule exactly
  3.  8 GiB plan matches the frozen schedule exactly
  4.  correct warmup/counted/physical launch counts per budget
  5.  policy balance (20/20 counted per policy) per budget
  6.  ABBA ordering per cell, per budget
  7.  opener balance per budget
  8.  correct per-budget B_selected (aged_frequency) params
  9.  correct PredictedProtect params (budget-independent predictor decay)
  10. deterministic run IDs
  11. canonical resume remains budget-specific (no cross-budget run_id reuse)
  12. a 4 GiB result can never satisfy a 1.5/8 GiB run
  13. precanonical collision still fails closed
  14. trace-env inheritance regression remains green
  15. disk guard remains green
  16. session/budget mismatch fails before inference
  17. frozen digest remains unchanged

Entry point: run_budget_extension_tests() -> {"ok": bool, "sub_results": {...}}.
Also runnable standalone: `python3 tools/r2_12_budget_extension_test.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_12_benchmark_driver as drv  # noqa: E402

FROZEN_BUDGETS = (1.5, 4.0, 8.0)


def _check_1_4gib_plan_unchanged(frozen, results):
    cells_old, launches_old = drv.build_4gib_plan(frozen)
    cells_new, launches_new = drv.build_budget_plan(frozen, 4.0)
    ok = (cells_old == cells_new) and (launches_old == launches_new) and (len(launches_old) == 48)
    results["1_4gib_plan_unchanged"] = ok
    return ok, launches_old


def _check_2_3_plans_match_frozen_schedule(frozen, results):
    ok = True
    for bgib in (1.5, 8.0):
        cells, _ = drv.build_budget_plan(frozen, bgib)
        frozen_cells = [c for c in frozen["run_order"]["cell_sequence_table"] if c["budget_gib"] == bgib]
        match = (cells == frozen_cells) and len(cells) == 4
        results[f"plan_{bgib}gib_matches_frozen_cell_sequence_table"] = match
        ok = ok and match
    return ok


def _check_4_launch_counts(frozen, results):
    ok = True
    for bgib in FROZEN_BUDGETS:
        _, launches = drv.build_budget_plan(frozen, bgib)
        n_warmup = sum(1 for l in launches if l["ordering_block"] == "warmup")
        n_counted = sum(1 for l in launches if l["ordering_block"] == "counted")
        this_ok = len(launches) == 48 and n_warmup == 8 and n_counted == 40
        results[f"launch_counts_{bgib}gib"] = {
            "ok": this_ok, "physical": len(launches), "warmup": n_warmup, "counted": n_counted,
        }
        ok = ok and this_ok
    return ok


def _check_5_policy_balance(frozen, results):
    ok = True
    for bgib in FROZEN_BUDGETS:
        _, launches = drv.build_budget_plan(frozen, bgib)
        n_a = sum(1 for l in launches if l["counted"] and l["policy_id"] == "b_selected")
        n_b = sum(1 for l in launches if l["counted"] and l["policy_id"] == "predicted_protect")
        this_ok = n_a == n_b == 20
        results[f"policy_balance_{bgib}gib"] = {"ok": this_ok, "b_selected": n_a, "predicted_protect": n_b}
        ok = ok and this_ok
    return ok


def _check_6_abba_ordering(frozen, results):
    seq_a = frozen["run_order"]["within_cell_ordering"]["sequence_if_opening_A"]
    seq_b = frozen["run_order"]["within_cell_ordering"]["sequence_if_opening_B"]
    ok = True
    for bgib in FROZEN_BUDGETS:
        cells, _ = drv.build_budget_plan(frozen, bgib)
        for c in cells:
            expected = seq_a if c["opening_policy"] == "A" else seq_b
            this_ok = c["sequence"] == expected
            results.setdefault("abba_ordering_per_cell", {})[f"{bgib}gib/{c['workload_id']}"] = this_ok
            ok = ok and this_ok
    return ok


def _check_7_opener_balance(frozen, results):
    ok = True
    for bgib in FROZEN_BUDGETS:
        cells, _ = drv.build_budget_plan(frozen, bgib)
        n_a = sum(1 for c in cells if c["opening_policy"] == "A")
        n_b = sum(1 for c in cells if c["opening_policy"] == "B")
        this_ok = n_a == n_b == 2
        results[f"opener_balance_{bgib}gib"] = {"ok": this_ok, "opening_A": n_a, "opening_B": n_b}
        ok = ok and this_ok
    return ok


def _check_8_9_per_budget_policy_params(frozen, results):
    # Independently re-derived from the raw frozen contract dict (not
    # through the driver's own helper) to avoid a tautological check.
    raw_mapping = frozen["contract"]["frozen_baseline"]["mapping_by_budget_bytes"]
    expected_by_bytes = {
        int(k): (v["params"]["decay_factor"], v["params"]["aging_interval_steps"])
        for k, v in raw_mapping.items()
    }
    raw_predictor_decay = frozen["contract"]["frozen_predictor"]["decay_factor"]

    ok = True
    for bgib in FROZEN_BUDGETS:
        bb = drv.budget_gib_to_bytes(bgib)
        params = drv.resolve_baseline_params_for_budget(frozen, bb)
        expected_decay, expected_interval = expected_by_bytes[bb]
        b_selected_ok = (params["decay_factor"] == expected_decay
                          and params["aging_interval_steps"] == expected_interval)
        results[f"b_selected_params_{bgib}gib"] = {
            "ok": b_selected_ok, "decay_factor": params["decay_factor"],
            "aging_interval_steps": params["aging_interval_steps"],
        }
        ok = ok and b_selected_ok

        predictor_decay = drv.resolve_predictor_decay_factor(frozen)
        predicted_protect_ok = predictor_decay == raw_predictor_decay == 0.99
        results[f"predicted_protect_predictor_decay_{bgib}gib"] = {
            "ok": predicted_protect_ok, "predictor_decay_factor": predictor_decay,
        }
        ok = ok and predicted_protect_ok

        # End-to-end: the actual env dict produced for this budget carries
        # the right values for BOTH policies.
        env_a = drv.build_child_env("b_selected", "S", "T", bb, 128,
                                     params["decay_factor"], params["aging_interval_steps"], predictor_decay)
        env_b = drv.build_child_env("predicted_protect", "S", "T", bb, 128,
                                     params["decay_factor"], params["aging_interval_steps"], predictor_decay)
        env_ok = (
            env_a["ARGINE_RETENTION_DECAY"] == str(expected_decay)
            and env_a["ARGINE_RETENTION_INTERVAL"] == str(expected_interval)
            and env_b["ARGINE_RETENTION_DECAY"] == str(expected_decay)
            and env_b["ARGINE_RETENTION_INTERVAL"] == str(expected_interval)
            and env_b["ARGINE_RETENTION_PREDICTOR_DECAY"] == str(raw_predictor_decay)
            and env_b["ARGINE_RETENTION_TOPK"] == "8"
            and env_b["ARGINE_RETENTION_NEXPERTS"] == "128"
        )
        results[f"child_env_params_{bgib}gib"] = env_ok
        ok = ok and env_ok
    return ok


def _check_10_deterministic_run_ids(frozen, results):
    ok = True
    for bgib in FROZEN_BUDGETS:
        _, l1 = drv.build_budget_plan(frozen, bgib)
        _, l2 = drv.build_budget_plan(frozen, bgib)
        ids1 = [l["run_id"] for l in l1]
        ids2 = [l["run_id"] for l in l2]
        this_ok = ids1 == ids2 and len(set(ids1)) == len(ids1)
        results[f"deterministic_run_ids_{bgib}gib"] = this_ok
        ok = ok and this_ok
    return ok


def _check_11_12_canonical_resume_budget_specific(frozen, canonical_4gib_launches, results):
    all_run_ids_by_budget = {}
    for bgib in FROZEN_BUDGETS:
        _, launches = drv.build_budget_plan(frozen, bgib)
        all_run_ids_by_budget[bgib] = {l["run_id"] for l in launches}

    collisions = {}
    budgets = list(all_run_ids_by_budget)
    for i in range(len(budgets)):
        for j in range(i + 1, len(budgets)):
            overlap = all_run_ids_by_budget[budgets[i]] & all_run_ids_by_budget[budgets[j]]
            if overlap:
                collisions[(budgets[i], budgets[j])] = sorted(overlap)
    no_cross_budget_collision = not collisions
    results["no_cross_budget_run_id_collision"] = {"ok": no_cross_budget_collision, "collisions": collisions}

    # Item 12, concretely: none of the real sealed 4 GiB canonical run_ids
    # (on disk, canonical=true) appear in the 1.5/8 GiB plans' run_id sets.
    canonical_4gib_run_ids = set()
    for fp in drv.RESULTS_DIR.glob("*.json"):
        try:
            rec = json.loads(fp.read_text())
        except Exception:
            continue
        if rec.get("canonical") is True and rec.get("budget_gib") == 4.0:
            canonical_4gib_run_ids.add(rec["run_id"])

    leaks = {}
    for bgib in (1.5, 8.0):
        overlap = canonical_4gib_run_ids & all_run_ids_by_budget[bgib]
        if overlap:
            leaks[bgib] = sorted(overlap)
    no_4gib_result_satisfies_other_budget = not leaks
    results["4gib_canonical_result_never_satisfies_other_budget_run"] = {
        "ok": no_4gib_result_satisfies_other_budget,
        "n_canonical_4gib_checked": len(canonical_4gib_run_ids),
        "leaks": leaks,
    }
    ok = no_cross_budget_collision and no_4gib_result_satisfies_other_budget
    return ok


def _check_13_precanonical_collision_fails_closed(results):
    skip = drv.classify_existing_record_for_resume({"ok": True, "canonical": True})
    halt_precanonical = drv.classify_existing_record_for_resume({"ok": True, "canonical": False})
    halt_precanonical_missing_field = drv.classify_existing_record_for_resume({"ok": True})
    halt_failed = drv.classify_existing_record_for_resume({"ok": False, "canonical": False})
    ok = (skip == "skip" and halt_precanonical == "halt_precanonical"
          and halt_precanonical_missing_field == "halt_precanonical" and halt_failed == "halt_failed")
    results["precanonical_collision_fails_closed"] = {
        "ok": ok, "skip": skip, "halt_precanonical": halt_precanonical,
        "halt_precanonical_missing_field": halt_precanonical_missing_field, "halt_failed": halt_failed,
    }
    return ok


def _check_14_trace_env_inheritance_regression(results):
    from r2_12_benchmark_driver_selftest import run_inherited_env_regression_test
    r = run_inherited_env_regression_test()
    results["trace_env_inheritance_regression"] = r
    return bool(r.get("ok"))


def _check_15_disk_guard(results):
    ok = True
    pass_check = drv.check_disk_space_margin(drv.WORK_DIR)
    results["disk_guard_pass_case"] = pass_check
    ok = ok and pass_check["ok"]

    old_margin = drv.MIN_FREE_DISK_BYTES_CAMPAIGN_START
    drv.MIN_FREE_DISK_BYTES_CAMPAIGN_START = 10 ** 18
    try:
        fail_check = drv.check_disk_space_margin(drv.WORK_DIR)
        results["disk_guard_fail_case"] = fail_check
        ok = ok and (fail_check["ok"] is False)
    finally:
        drv.MIN_FREE_DISK_BYTES_CAMPAIGN_START = old_margin
    return ok


def _check_16_session_budget_mismatch_fails_before_inference(results):
    class FakeArgs:
        pass

    attempted_real_calls = {"count": 0}
    old_load = drv.load_frozen_protocol

    def tripwire(*a, **kw):
        attempted_real_calls["count"] += 1
        return old_load(*a, **kw)

    drv.load_frozen_protocol = tripwire
    try:
        args = FakeArgs()
        args.budget_gib = 1.5
        args.confirm_session = "4gib-primary"  # WRONG session for 1.5 GiB -- must be refused
        args.i_understand_this_starts_real_inference = True
        args.runtime = "unused"
        args.model = "unused"
        args.store = "unused"
        args.port = 0
        args.operator_attested_clean_environment = True
        rc = drv.cmd_execute(args)
        mismatch_refused = rc == 2 and attempted_real_calls["count"] == 0
        results["session_budget_mismatch_refused_before_frozen_load"] = {
            "ok": mismatch_refused, "returncode": rc, "load_frozen_protocol_calls": attempted_real_calls["count"],
        }

        # Sanity: the matching session for the same budget would proceed
        # past this specific check (we don't let it go any further -- that
        # would need real runtime/model/store paths and is out of scope
        # for this gate test).
        attempted_real_calls["count"] = 0
        args2 = FakeArgs()
        args2.budget_gib = 1.5
        args2.confirm_session = "1.5gib-stress"  # correct session
        args2.i_understand_this_starts_real_inference = False  # still withhold the OTHER required flag
        args2.runtime = "unused"
        args2.model = "unused"
        args2.store = "unused"
        args2.port = 0
        args2.operator_attested_clean_environment = True
        rc2 = drv.cmd_execute(args2)
        correct_session_still_gated_by_other_flag = rc2 == 2
        results["correct_session_still_requires_second_flag"] = {
            "ok": correct_session_still_gated_by_other_flag, "returncode": rc2,
        }
    finally:
        drv.load_frozen_protocol = old_load

    return mismatch_refused and correct_session_still_gated_by_other_flag


def _check_17_frozen_digest_unchanged(frozen, results):
    try:
        digest = drv.verify_contract_digest()
        ok = bool(digest.get("valid"))
    except Exception as e:
        digest = {"valid": False, "error": f"{type(e).__name__}: {e}"}
        ok = False
    results["frozen_digest_verification"] = digest
    return ok


def run_budget_extension_tests() -> dict:
    results: dict = {}
    ok = True

    frozen = drv.load_frozen_protocol()

    r1, canonical_4gib_launches = _check_1_4gib_plan_unchanged(frozen, results)
    ok = ok and r1

    ok = ok and _check_2_3_plans_match_frozen_schedule(frozen, results)
    ok = ok and _check_4_launch_counts(frozen, results)
    ok = ok and _check_5_policy_balance(frozen, results)
    ok = ok and _check_6_abba_ordering(frozen, results)
    ok = ok and _check_7_opener_balance(frozen, results)
    ok = ok and _check_8_9_per_budget_policy_params(frozen, results)
    ok = ok and _check_10_deterministic_run_ids(frozen, results)
    ok = ok and _check_11_12_canonical_resume_budget_specific(frozen, canonical_4gib_launches, results)
    ok = ok and _check_13_precanonical_collision_fails_closed(results)

    try:
        ok14 = _check_14_trace_env_inheritance_regression(results)
    except Exception as e:
        results["trace_env_inheritance_regression"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        ok14 = False
    ok = ok and ok14

    ok = ok and _check_15_disk_guard(results)
    ok = ok and _check_16_session_budget_mismatch_fails_before_inference(results)
    ok = ok and _check_17_frozen_digest_unchanged(frozen, results)

    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    print(json.dumps(run_budget_extension_tests(), indent=2, default=str))
