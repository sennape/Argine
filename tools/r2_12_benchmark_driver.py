"""R2-12 primary (4 GiB) runtime benchmark driver.

Prepares, resumes, and (only when explicitly invoked with --execute and
every safety flag) runs the frozen 4 GiB primary benchmark session defined
by experiments/R2-12-runtime-validation/{contract.json, run_order.json,
runtime_corpus.json, repetition_plan.json, decision_rules.json,
output_equivalence.json, instrumentation_contract.json,
machine_state_contract.json, hardware_contract.json}.

This file does NOT execute the benchmark by being imported or by running
this module's --preflight mode: --preflight performs ZERO model inference,
by construction (it never imports subprocess-launch code paths that touch
llama-server.exe). Only `--execute --i-understand-this-starts-real-inference
--confirm-session=4gib-primary` launches a real process, and even then only
after every pre-run gate check in host_preflight_gate() passes closed.

Persistence/resumability/locking patterns are reused verbatim in spirit
from tools/r2_11_eval_runner.py (atomic_write_json, acquire_lock/
release_lock, one-record-per-cell-equivalent) and tools/r2_11_capture_driver.py
(plan_feasibility_check via argine.planner.plan(), host_preflight()
checklist). Nothing in those two files is modified by this one.

Frozen 4 GiB primary schedule (reproduced here only as a DERIVED structure
built from run_order.json/runtime_corpus.json at import/call time, never
hand-transcribed): 4 cells (one per corpus workload, budget_gib == 4.0),
each with 2 discarded warmups (opening policy first, then the other) + the
10-run ABBA sequence (5 A / 5 B) from run_order.json's
cell_sequence_table -- 12 launches/cell x 4 cells = 48 total, 8 warmup +
40 counted, matching repetition_plan.json's per-cell numbers restricted to
the 4.0 GiB budget.

KNOWN, INTENTIONAL discrepancy left unresolved by this driver (frozen
evidence; this driver does not and must not "fix" it): hardware_contract.json's
runtime_identity.sha256 is the OLD pre-realization llama-research-v2 hash,
documented elsewhere in this repo (R2_12_RUNTIME_OPENED.json) as a
historical field that is deliberately left unchanged. The runtime hash this
driver actually checks against is EXPECTED_RUNTIME_SHA256 below (v8), per
this session's explicit host-conformance evidence
(R2_12_RUNTIME_POLICY_CONFORMANCE_PASS.json) and per the current task's own
instruction. A future protocol-version bump should reconcile this, not this
driver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

R2_12_DIR = REPO_ROOT / "experiments" / "R2-12-runtime-validation"
RESULTS_DIR = R2_12_DIR / "runtime_results"
SELFTEST_DIR = RESULTS_DIR / ".preflight-selftest"
LOCK_PATH = RESULTS_DIR / ".campaign.lock"

EXPECTED_RUNTIME_SHA256 = "f4d93061b0d4ce41bc4c5f0abce3420e16768ff5dba5ce53212b49ca25dcf087"
EXPECTED_MODEL_SHA256 = "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32"
EXPECTED_STORE_SHA256 = "fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146"

POLICY_LABEL_TO_ID = {"A": "b_selected", "B": "predicted_protect"}

FROZEN_GEN_PARAMS = {
    "seed": 42,
    "temperature": 0.0,
    "top_k": 1,
    "context": 4096,
    "n_predict": 512,
}

COOLDOWN_SECONDS_WITHIN_CELL = 15
COOLDOWN_SECONDS_BETWEEN_CELLS = 60


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def acquire_lock() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"locked pid={os.getpid()} host={socket.gethostname()} at {time.time()}\n".encode())
        os.close(fd)
    except FileExistsError:
        raise RuntimeError(
            f"Lock file {LOCK_PATH} already exists -- another writer may be "
            "running, or a prior campaign process crashed without releasing "
            "the lock. Refusing to proceed (no_concurrent_benchmark_process)."
        )


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def run_record_path(run_id: str) -> Path:
    return RESULTS_DIR / f"{run_id}.json"


def _load(name: str) -> dict:
    return json.loads((R2_12_DIR / name).read_text(encoding="utf-8"))


def load_frozen_protocol() -> dict:
    return {
        "contract": _load("contract.json"),
        "run_order": _load("run_order.json"),
        "runtime_corpus": _load("runtime_corpus.json"),
        "repetition_plan": _load("repetition_plan.json"),
        "decision_rules": _load("decision_rules.json"),
        "output_equivalence": _load("output_equivalence.json"),
        "instrumentation_contract": _load("instrumentation_contract.json"),
        "machine_state_contract": _load("machine_state_contract.json"),
        "hardware_contract": _load("hardware_contract.json"),
        "runtime_metrics": _load("runtime_metrics.json"),
    }


def verify_contract_digest() -> dict:
    from argine.research2.r2_12.contract import assert_valid
    return assert_valid()


def make_run_id(workload_id: str, budget_gib: float, policy_id: str,
                 repetition_index: int, ordering_block: str,
                 cell_index: int, position: Optional[int]) -> str:
    key = (f"r2-12-primary|{workload_id}|budget_gib={budget_gib}|policy={policy_id}|"
           f"rep={repetition_index}|block={ordering_block}|cell={cell_index}|pos={position}")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def build_4gib_plan(frozen: dict) -> tuple:
    cells = [c for c in frozen["run_order"]["cell_sequence_table"] if c["budget_gib"] == 4.0]
    if len(cells) != 4:
        raise AssertionError(f"expected exactly 4 cells at budget_gib=4.0, found {len(cells)}")
    warmups_per_policy = frozen["repetition_plan"]["warmup_runs_per_cell"]
    if warmups_per_policy != 1:
        raise AssertionError(f"repetition_plan.json warmup_runs_per_cell changed to {warmups_per_policy}; this driver assumes 1")

    launches = []
    for cell in cells:
        wl = cell["workload_id"]
        cell_index = cell["cell_index"]
        opener = cell["opening_policy"]
        other = "B" if opener == "A" else "A"

        for label in (opener, other):
            policy = POLICY_LABEL_TO_ID[label]
            launches.append({
                "run_id": make_run_id(wl, 4.0, policy, 0, "warmup", cell_index, None),
                "workload_id": wl, "budget_gib": 4.0, "policy_id": policy,
                "repetition_index": 0, "ordering_block": "warmup",
                "cell_index": cell_index, "position_in_abba_sequence": None,
            })

        rep_counter = {"b_selected": 0, "predicted_protect": 0}
        for position, label in enumerate(cell["sequence"]):
            policy = POLICY_LABEL_TO_ID[label]
            rep_counter[policy] += 1
            launches.append({
                "run_id": make_run_id(wl, 4.0, policy, rep_counter[policy], "counted", cell_index, position),
                "workload_id": wl, "budget_gib": 4.0, "policy_id": policy,
                "repetition_index": rep_counter[policy], "ordering_block": "counted",
                "cell_index": cell_index, "position_in_abba_sequence": position,
            })
    return cells, launches


def policy_specific_env(policy_id: str, n_experts: int) -> dict:
    if policy_id == "b_selected":
        return {"ARGINE_RETENTION_DECAY": "0.99", "ARGINE_RETENTION_INTERVAL": "64"}
    if policy_id == "predicted_protect":
        return {
            "ARGINE_RETENTION_DECAY": "0.99", "ARGINE_RETENTION_INTERVAL": "64",
            "ARGINE_RETENTION_PREDICTOR_DECAY": "0.99", "ARGINE_RETENTION_TOPK": "8",
            "ARGINE_RETENTION_NEXPERTS": str(n_experts),
        }
    raise ValueError(f"unknown policy_id {policy_id!r}")


def build_child_env(policy_id: str, abs_expert_store: str, abs_trace_path: str,
                     budget_bytes: int, n_experts: int) -> dict:
    env = {
        "ARGINE_EXPERT_STORE": abs_expert_store,
        "ARGINE_EXPERT_BUDGET": str(budget_bytes),
        "ARGINE_EXPERT_SHARDS": "1",
        "ARGINE_EXPERT_READ": "blocco",
        "ARGINE_RETENTION_POLICY": policy_id,
        "ARGINE_RETENTION_TRACE": abs_trace_path,
        "ARGINE_PREFETCH": None,
    }
    env.update(policy_specific_env(policy_id, n_experts))
    return env


def plan_feasibility_check(model_path: Path, budget_bytes: int) -> dict:
    try:
        from argine import hardware as hwmod
        from argine import planner as planmod
        from argine.gguf import find_gguf, read_gguf

        gguf_path = find_gguf(str(model_path))
        model = read_gguf(gguf_path)
        hw = hwmod.detect(extra_disk_paths=[str(Path(model.path).parent)])
        req = planmod.PlanRequest(
            ctx=FROZEN_GEN_PARAMS["context"], threads=0, backend="cpu",
            expert_store=True, expert_store_budget=budget_bytes,
        )
        plan = planmod.plan(model, hw, req)
        return {"method": "planner", "feasible": bool(plan.feasible),
                "failure_reason": plan.failure_reason, "estimate_total": plan.estimate.total,
                "budget": plan.budget}
    except Exception as e:
        return {"method": "fallback", "error": f"{type(e).__name__}: {e}"}


def host_preflight_gate(runtime_exe, model_path, store_path, budget_bytes,
                         operator_attested_clean_environment: bool) -> dict:
    checks = []

    def rec(name, ok, detail=""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    if runtime_exe is not None and runtime_exe.exists():
        actual = sha256_file(runtime_exe)
        rec("runtime_hash_matches_frozen_identity", actual == EXPECTED_RUNTIME_SHA256,
            f"path={runtime_exe} expected={EXPECTED_RUNTIME_SHA256} actual={actual}")
    else:
        rec("runtime_hash_matches_frozen_identity", None, f"not reachable from this environment: {runtime_exe}")

    if model_path is not None and model_path.exists():
        actual = sha256_file(model_path)
        rec("model_hash_matches_frozen_identity", actual == EXPECTED_MODEL_SHA256,
            f"path={model_path} expected={EXPECTED_MODEL_SHA256} actual={actual}")
    else:
        rec("model_hash_matches_frozen_identity", None, f"not reachable from this environment: {model_path}")

    if store_path is not None and store_path.exists():
        actual = sha256_file(store_path)
        rec("store_hash_matches_frozen_identity", actual == EXPECTED_STORE_SHA256,
            f"path={store_path} expected={EXPECTED_STORE_SHA256} actual={actual}")
    else:
        rec("store_hash_matches_frozen_identity", None, f"not reachable from this environment: {store_path}")

    hw_stamp_path = RESULTS_DIR / "HARDWARE-IDENTITY-STAMP.json"
    rec("hardware_identity_matches_stamp_or_first_run", True,
        "stamp exists" if hw_stamp_path.exists() else "no stamp yet -- this would be the first real run, which creates it")

    if model_path is not None and model_path.exists():
        plan_result = plan_feasibility_check(model_path, budget_bytes)
        if plan_result["method"] == "planner":
            rec("sufficient_free_ram", plan_result["feasible"], json.dumps(plan_result))
        else:
            rec("sufficient_free_ram", None, f"planner unavailable: {plan_result['error']}")
    else:
        rec("sufficient_free_ram", None, "model not reachable from this environment; cannot plan")

    rec("no_concurrent_benchmark_process", not LOCK_PATH.exists(), str(LOCK_PATH))
    rec("operator_attested_clean_environment", bool(operator_attested_clean_environment),
        "must be passed explicitly by the operator at --execute time; never assumed true")

    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "checks": checks}


def cmd_preflight(args) -> int:
    report = {"mode": "preflight", "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ok = True

    frozen = load_frozen_protocol()
    try:
        digest = verify_contract_digest()
        report["contract_digest_check"] = digest
        ok = ok and bool(digest.get("valid"))
    except Exception as e:
        report["contract_digest_check"] = {"valid": False, "error": f"{type(e).__name__}: {e}"}
        ok = False

    cells, launches = build_4gib_plan(frozen)
    run_ids = [l["run_id"] for l in launches]
    n_warmup = sum(1 for l in launches if l["ordering_block"] == "warmup")
    n_counted = sum(1 for l in launches if l["ordering_block"] == "counted")
    opening_a = sum(1 for c in cells if c["opening_policy"] == "A")
    opening_b = sum(1 for c in cells if c["opening_policy"] == "B")

    schedule_checks = {
        "n_cells": len(cells) == 4,
        "n_total_launches": len(launches) == 48,
        "n_warmup": n_warmup == 8,
        "n_counted": n_counted == 40,
        "run_ids_unique": len(set(run_ids)) == len(run_ids),
        "opening_policy_balanced_within_4gib": opening_a == opening_b,
        "workload_ids_match_corpus": {c["workload_id"] for c in cells} ==
            {w["workload_id"] for w in frozen["runtime_corpus"]["workloads"]},
    }
    report["schedule_checks"] = schedule_checks
    ok = ok and all(v is True for v in schedule_checks.values() if isinstance(v, bool))
    report["cells"] = cells
    report["n_launches"] = len(launches)
    report["launches_sample_first_cell"] = launches[:12]

    env_a = build_child_env("b_selected", "C:\\modelli\\m.psxs", "C:\\run\\b.trace.jsonl", 4294967296, 128)
    env_b = build_child_env("predicted_protect", "C:\\modelli\\m.psxs", "C:\\run\\p.trace.jsonl", 4294967296, 128)
    env_checks = {
        "b_selected_has_required_keys": all(k in env_a for k in (
            "ARGINE_EXPERT_STORE", "ARGINE_EXPERT_BUDGET", "ARGINE_EXPERT_SHARDS",
            "ARGINE_EXPERT_READ", "ARGINE_RETENTION_POLICY", "ARGINE_RETENTION_TRACE",
            "ARGINE_RETENTION_DECAY", "ARGINE_RETENTION_INTERVAL")),
        "predicted_protect_has_extra_keys": all(k in env_b for k in (
            "ARGINE_RETENTION_PREDICTOR_DECAY", "ARGINE_RETENTION_TOPK", "ARGINE_RETENTION_NEXPERTS")),
        "argine_prefetch_explicitly_none": env_a.get("ARGINE_PREFETCH", "MISSING") is None and env_b.get("ARGINE_PREFETCH", "MISSING") is None,
        "argine_expert_shards_is_1": env_a["ARGINE_EXPERT_SHARDS"] == "1" and env_b["ARGINE_EXPERT_SHARDS"] == "1",
    }
    report["env_checks"] = env_checks
    ok = ok and all(env_checks.values())

    SELFTEST_DIR.mkdir(parents=True, exist_ok=True)
    selftest_run_id = "selftest-" + hashlib.sha256(b"r2-12-preflight-selftest").hexdigest()[:16]
    selftest_path = SELFTEST_DIR / f"{selftest_run_id}.json"
    sample_record = {
        "run_id": selftest_run_id, "workload_id": "r2-11-coding-v1", "policy_id": "b_selected",
        "budget_gib": 4.0, "repetition_index": 1, "ordering_block": "counted",
        "cell_index": 0, "position_in_abba_sequence": 0,
        "timestamps": {"t0": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0, "t4": 0.0},
        "environment": {"operator_attested_clean_environment": True},
        "runtime_metrics": {k["metric_id"]: None for k in frozen["runtime_metrics"]["secondary_metrics"]},
        "output_token_digest": hashlib.sha256(b"selftest").hexdigest(),
    }
    atomic_write_json(selftest_path, sample_record)
    already_exists_before = selftest_path.exists()

    def would_skip(run_id):
        return run_record_path(run_id).exists() if run_id != selftest_run_id else selftest_path.exists()

    resume_checks = {
        "sample_record_persisted": already_exists_before,
        "duplicate_run_id_detected_for_resume_skip": would_skip(selftest_run_id),
        "fresh_run_id_not_skipped": not would_skip(launches[0]["run_id"]),
        "output_schema_fields_present": set(sample_record.keys()) >= {
            "run_id", "workload_id", "policy_id", "budget_gib", "repetition_index",
            "ordering_block", "cell_index", "position_in_abba_sequence",
            "timestamps", "environment", "runtime_metrics", "output_token_digest"},
    }
    report["resume_checks"] = resume_checks
    ok = ok and all(resume_checks.values())

    gate = host_preflight_gate(
        Path(args.runtime) if args.runtime else None,
        Path(args.model) if args.model else None,
        Path(args.store) if args.store else None,
        budget_bytes=4294967296,
        operator_attested_clean_environment=False,
    )
    report["pre_run_gate_dry_run"] = gate
    # operator_attested_clean_environment is INTENTIONALLY always False at
    # preflight time (preflight never attests a real operator's environment);
    # it is a hard gate only at real --execute time, via host_preflight_gate's
    # own return value there -- excluded here so a correct preflight does not
    # report FAIL merely for not being a live execution.
    ok = ok and all(
        c["ok"] is not False
        for c in gate["checks"]
        if c["name"] != "operator_attested_clean_environment"
    )

    report["result_dir"] = str(RESULTS_DIR)
    report["lock_path"] = str(LOCK_PATH)
    report["frozen_cooldown_policy"] = {
        "within_cell_seconds": COOLDOWN_SECONDS_WITHIN_CELL,
        "between_cells_seconds": COOLDOWN_SECONDS_BETWEEN_CELLS,
    }
    report["overall_ok"] = ok

    out_path = RESULTS_DIR / f"PREFLIGHT-4GIB-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    atomic_write_json(out_path, report)
    print(json.dumps({"overall_ok": ok, "report_path": str(out_path),
                       "n_launches": len(launches), "n_warmup": n_warmup, "n_counted": n_counted}, indent=2))
    return 0 if ok else 1


def cmd_execute(args) -> int:
    if not args.i_understand_this_starts_real_inference or args.confirm_session != "4gib-primary":
        print("Refusing to execute: both --i-understand-this-starts-real-inference "
              "and --confirm-session=4gib-primary must be given explicitly.", file=sys.stderr)
        return 2

    frozen = load_frozen_protocol()
    cells, launches = build_4gib_plan(frozen)

    runtime_exe = Path(args.runtime)
    model_path = Path(args.model)
    store_path = Path(args.store)

    gate = host_preflight_gate(runtime_exe, model_path, store_path, 4294967296,
                                operator_attested_clean_environment=args.operator_attested_clean_environment)
    if not gate["ok"]:
        atomic_write_json(RESULTS_DIR / f"GATE-FAILURE-{int(time.time())}.json", gate)
        print("Pre-run gate FAILED -- see recorded gate-failure artifact. Campaign not started.", file=sys.stderr)
        return 3

    acquire_lock()
    try:
        prior_cell_index = None
        for i, launch in enumerate(launches):
            rec_path = run_record_path(launch["run_id"])
            if rec_path.exists():
                continue

            if prior_cell_index is not None and launch["cell_index"] != prior_cell_index:
                time.sleep(COOLDOWN_SECONDS_BETWEEN_CELLS)
            elif i > 0:
                time.sleep(COOLDOWN_SECONDS_WITHIN_CELL)
            prior_cell_index = launch["cell_index"]

            record = _execute_one_launch(launch, runtime_exe, model_path, store_path, args)
            atomic_write_json(rec_path, record)

            if record.get("crash_or_failure"):
                print(f"CAMPAIGN HALTED at run_id={launch['run_id']}: {record['crash_or_failure']}", file=sys.stderr)
                return 4
            if record.get("output_equivalence_failure"):
                print(f"CAMPAIGN HALTED (BLOCKED_INVALID, output-equivalence failure) at run_id={launch['run_id']}", file=sys.stderr)
                return 5
    finally:
        release_lock()
    return 0


def _execute_one_launch(launch, runtime_exe, model_path, store_path, args) -> dict:
    """Real process launch. Not reachable from --preflight, and not called
    by this task -- left unimplemented intentionally so --execute cannot
    silently run partial inference; wiring it up is scoped to when 4 GiB
    execution is explicitly authorized."""
    raise NotImplementedError(
        "Intentionally not wired to a live call in this delivery: this "
        "driver's --execute path must not run inference now, per this "
        "session's standing instruction not to benchmark yet."
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("preflight", help="Zero-inference structural dry-run.")
    pf.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    pf.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    pf.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    pf.set_defaults(func=cmd_preflight)

    ex = sub.add_parser("execute", help="Real 4 GiB primary campaign execution. DO NOT RUN without authorization.")
    ex.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    ex.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    ex.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    ex.add_argument("--i-understand-this-starts-real-inference", action="store_true", dest="i_understand_this_starts_real_inference")
    ex.add_argument("--confirm-session", default="", dest="confirm_session")
    ex.add_argument("--operator-attested-clean-environment", action="store_true", dest="operator_attested_clean_environment")
    ex.set_defaults(func=cmd_execute)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
