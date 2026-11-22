"""R2-11 confirmatory evaluation runner (Phase 2, post-EVAL_OPENED).

Replays the 12 sealed R2-11 traces through the two frozen policies
(PredictedProtectPolicy, B_selected(M)/AgedFrequency) at the three frozen
budgets, using argine.research2.r2_09a.fast_replay.run_fast -- the same
replay engine already used, unmodified, for B_selected in R2-09A and
R2-BRIDGE-01. No model inference is involved: this is pure deterministic
CPU replay of already-captured, already-sealed trace files, so it can run
anywhere argine is importable and the trace files are reachable.

Resumable, atomic, duplicate-writer-safe: each of the 72 cells is persisted
as its own JSON file under experiments/R2-11-predictive-retention/eval_cells/
immediately after computation (atomic write-then-rename), and a cell whose
file already exists with a matching input trace SHA-256 is skipped on
re-run rather than recomputed. A lock file prevents two concurrent writers.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from argine.research.replay import InvariantViolation
from argine.research.trace import PHASE_DECODE, Trace
from argine.research2.r2_09a.fast_replay import run_fast
from argine.research2.r2_09a.policies import AgedFrequency
from argine.research2.r2_bridge01.retention_policies import PredictedProtectPolicy

EVAL_DIR = REPO_ROOT / "experiments" / "R2-11-predictive-retention"
CELLS_DIR = EVAL_DIR / "eval_cells"
CAPTURES_BASE = (REPO_ROOT / ".." / "Local--Argine3" / "research" / "R2-11" / "captures").resolve()
LOCK_PATH = CELLS_DIR / ".lock"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)  # atomic on same filesystem


def acquire_lock():
    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"locked pid={os.getpid()} at {time.time()}\n".encode())
        os.close(fd)
    except FileExistsError:
        raise RuntimeError(
            f"Lock file {LOCK_PATH} already exists -- another writer may be running, "
            "or a prior run crashed without releasing the lock. Refusing to proceed "
            "to avoid duplicate/concurrent writers."
        )


def release_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def cell_path(workload_id: str, budget_gib: float, policy_id: str) -> Path:
    return CELLS_DIR / f"{workload_id}__budget-{budget_gib}__{policy_id}.json"


def run_one_cell(workload_id: str, trace: Trace, trace_sha256: str, budget_bytes: int,
                  budget_gib: float, policy_id: str, decay_factor: float,
                  aging_interval_steps: int) -> dict:
    t0 = time.time()
    validity_failure = None
    det = None
    try:
        if policy_id == "predicted_protect":
            policy = PredictedProtectPolicy(decay_factor, aging_interval_steps)
        elif policy_id == "b_selected":
            policy = AgedFrequency(decay_factor, aging_interval_steps)
        else:
            raise ValueError(policy_id)
        det, _ = run_fast(trace, policy, budget_bytes, phase=PHASE_DECODE)
    except InvariantViolation as e:
        validity_failure = f"InvariantViolation: {e}"
    except Exception as e:  # noqa: BLE001
        validity_failure = f"{type(e).__name__}: {e}"
    elapsed = time.time() - t0

    cell = {
        "workload_id": workload_id,
        "input_trace_sha256": trace_sha256,
        "budget_bytes": budget_bytes,
        "budget_gib": budget_gib,
        "policy_id": policy_id,
        "policy_config": {"decay_factor": decay_factor, "aging_interval_steps": aging_interval_steps},
        "elapsed_seconds": elapsed,
        "validity_failure": validity_failure,
        "budget_violation": validity_failure is not None and "InvariantViolation" in (validity_failure or ""),
        "result": det,
    }
    if det is not None:
        steps = det["steps"]
        cell["miss_bytes_per_decode_step"] = (det["bytes_missed"] / steps) if steps else None
        cell["misses_per_decode_step"] = (det["misses"] / steps) if steps else None
    return cell


def main():
    acquire_lock()
    try:
        em = json.load(open(EVAL_DIR / "eval_manifest.json"))
        specs = em["eval_workload_specs"]
        bm = json.load(open(EVAL_DIR / "baseline_mapping.json"))
        budget_map = bm["mapping_by_budget_bytes"]  # keys are string byte counts

        n_computed = 0
        n_skipped = 0
        n_failed = 0

        for spec in specs:
            wl_id = spec["workload_id"]
            trace_path = CAPTURES_BASE / wl_id / "run-00-4G" / "trace.jsonl.gz"
            trace_sha256 = sha256_file(trace_path)
            trace = None  # lazy-load once per workload if any cell needs recompute

            for budget_bytes_str, params in budget_map.items():
                budget_bytes = int(budget_bytes_str)
                budget_gib = params["budget_gib"]
                decay_factor = params["params"]["decay_factor"]
                aging_interval_steps = params["params"]["aging_interval_steps"]

                for policy_id in ("predicted_protect", "b_selected"):
                    cp = cell_path(wl_id, budget_gib, policy_id)
                    if cp.exists():
                        existing = json.loads(cp.read_text())
                        if existing.get("input_trace_sha256") == trace_sha256 and existing.get("validity_failure") is None:
                            n_skipped += 1
                            continue
                    if trace is None:
                        trace = Trace.load(trace_path)
                    cell = run_one_cell(wl_id, trace, trace_sha256, budget_bytes, budget_gib,
                                        policy_id, decay_factor, aging_interval_steps)
                    atomic_write_json(cp, cell)
                    if cell["validity_failure"] is not None:
                        n_failed += 1
                        print(f"  [FAIL] {wl_id} budget={budget_gib}GiB policy={policy_id}: {cell['validity_failure']}")
                    else:
                        n_computed += 1
                        print(f"  [OK]   {wl_id} budget={budget_gib}GiB policy={policy_id} "
                              f"mbps={cell['miss_bytes_per_decode_step']:.4f} "
                              f"evictions={cell['result']['evictions']} "
                              f"peak_resident={cell['result']['peak_resident']}")

        print()
        print(f"=== DONE: computed={n_computed} skipped(reused)={n_skipped} failed={n_failed} "
              f"(expected total=72) ===")
        return 0 if n_failed == 0 else 1
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
