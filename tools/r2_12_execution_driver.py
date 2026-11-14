"""R2-12 -- resumable runtime execution driver (PREPARED, NOT EXECUTABLE YET).

This module implements the frozen 4-workload x 3-budget x 2-policy x
5-counted-repetition (+1 discarded warmup per policy) matrix exactly as
frozen in run_order.json / runtime_corpus.json / repetition_plan.json --
144 total process launches (120 counted + 24 discarded warmup), grouped
into three separable 40-counted-execution budget sessions (4 GiB primary,
1.5 GiB stress, 8 GiB null/control).

By deliberate design, real measured execution is NOT implemented here:
`_execute_cell_NOT_IMPLEMENTED()` always raises. This is not a flag that
can be flipped by a CLI argument -- the real execution path simply does
not exist in this file yet, so this script cannot run a real benchmark
no matter how it is invoked. That is intentional: R2_12_RUNTIME_OPENED
does not exist, and real execution must not begin before that separate,
explicit commit exists (execution_plan.json.r2_12_runtime_opened_gate).

What IS implemented and testable now, with zero scientific side effects:
  - exact matrix generation (144 RunSpecs) from the frozen artifacts,
    never duplicating their content by hand;
  - the three separable 40-counted-execution budget sessions, preserving
    the frozen relative cell order and the frozen ABBA/alternating-opener
    sequence and warmup-ordering within each cell;
  - atomic per-run persistence (write-to-tmp then os.replace, same
    pattern as tools/r2_11_eval_runner.py);
  - duplicate-run-ID prevention (a run_id already on disk with a matching
    recorded configuration is skipped on resume; a run_id on disk with a
    MISMATCHED configuration raises loudly rather than being overwritten);
  - a campaign-duration exclusive lock file (O_CREAT|O_EXCL), the same
    path tools/r2_12_readiness_driver.py's no_concurrent_writer check
    already looks for;
  - a `--mock` mode that writes clearly-tagged SYNTHETIC placeholder
    records (synthetic_test_record=True, real_execution=False, no actual
    timing data) so the persistence/resumability/session-partition logic
    can be exercised and unit-tested without any real or fabricated
    scientific result ever being produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CONTRACT_DIR = REPO_ROOT / "experiments" / "R2-12-runtime-validation"
RESULTS_DIR = CONTRACT_DIR / "runtime_results"
LOCK_PATH = REPO_ROOT / "tools" / ".r2_12_execution_driver.lock"

# Matches run_order.json.policy_labels exactly; letter kept alongside the
# r2_11_eval_runner-style policy_id string for cross-referencing the
# frozen ABBA sequence directly.
POLICY_LETTER_TO_ID = {"A": "b_selected", "B": "predicted_protect"}
BUDGET_GIB_TO_BYTES = {1.5: 1610612736, 4.0: 4294967296, 8.0: 8589934592}

ORDERING_WARMUP = "warmup"
ORDERING_COUNTED = "counted"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    workload_id: str
    budget_gib: float
    budget_bytes: int
    policy_letter: str  # "A" or "B", per the frozen run_order.json sequence
    policy_id: str       # "b_selected" / "predicted_protect"
    ordering_block: str  # "warmup" | "counted"
    cell_index: int
    position_in_cell: int  # 0..11 (0-1 = warmups, 2-11 = the 10-run counted sequence)
    repetition_index: Optional[int]  # 1..5 for counted runs of this policy within the cell; None for warmup


def _run_id_for(workload_id: str, budget_gib: float, policy_id: str,
                 ordering_block: str, cell_index: int, position_in_cell: int) -> str:
    """Deterministic, content-addressed run_id -- never a random uuid4, so
    that regenerating the plan from the same frozen inputs always produces
    the identical run_id for the identical logical run (required for
    resumability to work by simple file-existence lookup)."""
    material = json.dumps(
        {
            "workload_id": workload_id,
            "budget_gib": budget_gib,
            "policy_id": policy_id,
            "ordering_block": ordering_block,
            "cell_index": cell_index,
            "position_in_cell": position_in_cell,
        },
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def generate_full_plan(contract_dir: Optional[Path] = None) -> List[RunSpec]:
    """144 RunSpecs (24 warmup + 120 counted) in the exact frozen global
    order: outer=workload (runtime_corpus.json order), middle=budget_gib
    (4.0, 1.5, 8.0 -- run_order.json.cell_sequence_table's own order),
    inner=1 warmup per policy (opener first) then the frozen 10-run ABBA
    sequence for that cell. Nothing here is a hand-typed scientific
    constant; every value is read from the frozen artifacts."""
    d = contract_dir or CONTRACT_DIR
    run_order = load_json(d / "run_order.json")
    repetition_plan = load_json(d / "repetition_plan.json")

    assert repetition_plan["repetitions_per_cell"] == 5
    assert repetition_plan["warmup_runs_per_cell"] == 1

    plan: List[RunSpec] = []
    for cell in run_order["cell_sequence_table"]:
        workload_id = cell["workload_id"]
        budget_gib = cell["budget_gib"]
        budget_bytes = BUDGET_GIB_TO_BYTES[budget_gib]
        cell_index = cell["cell_index"]
        opening_letter = cell["opening_policy"]
        other_letter = "B" if opening_letter == "A" else "A"
        sequence = cell["sequence"]
        assert sequence[0] == opening_letter
        assert len(sequence) == 10 and sequence.count("A") == 5 and sequence.count("B") == 5

        position = 0
        # 1 discarded warmup per policy, opening policy first, then the other.
        for letter in (opening_letter, other_letter):
            policy_id = POLICY_LETTER_TO_ID[letter]
            run_id = _run_id_for(workload_id, budget_gib, policy_id, ORDERING_WARMUP, cell_index, position)
            plan.append(RunSpec(
                run_id=run_id, workload_id=workload_id, budget_gib=budget_gib,
                budget_bytes=budget_bytes, policy_letter=letter, policy_id=policy_id,
                ordering_block=ORDERING_WARMUP, cell_index=cell_index,
                position_in_cell=position, repetition_index=None,
            ))
            position += 1

        # The frozen 10-run counted ABBA sequence, in order.
        rep_counters = {"A": 0, "B": 0}
        for letter in sequence:
            rep_counters[letter] += 1
            policy_id = POLICY_LETTER_TO_ID[letter]
            run_id = _run_id_for(workload_id, budget_gib, policy_id, ORDERING_COUNTED, cell_index, position)
            plan.append(RunSpec(
                run_id=run_id, workload_id=workload_id, budget_gib=budget_gib,
                budget_bytes=budget_bytes, policy_letter=letter, policy_id=policy_id,
                ordering_block=ORDERING_COUNTED, cell_index=cell_index,
                position_in_cell=position, repetition_index=rep_counters[letter],
            ))
            position += 1

        assert position == 12

    assert len(plan) == 144
    assert sum(1 for r in plan if r.ordering_block == ORDERING_WARMUP) == 24
    assert sum(1 for r in plan if r.ordering_block == ORDERING_COUNTED) == 120
    return plan


def sessions_by_budget(plan: List[RunSpec]) -> Dict[float, List[RunSpec]]:
    """Partitions the full 144-run plan into the three separable budget
    sessions, preserving each run's position in the frozen global order
    (i.e. never reordering runs relative to each other -- only selecting
    a subset). Each session's counted-run count must be exactly 40."""
    sessions: Dict[float, List[RunSpec]] = {}
    for spec in plan:
        sessions.setdefault(spec.budget_gib, []).append(spec)
    for budget_gib, specs in sessions.items():
        counted = sum(1 for s in specs if s.ordering_block == ORDERING_COUNTED)
        assert counted == 40, f"budget {budget_gib} GiB session has {counted} counted runs, expected 40"
        warmups = sum(1 for s in specs if s.ordering_block == ORDERING_WARMUP)
        assert warmups == 8, f"budget {budget_gib} GiB session has {warmups} warmups, expected 8"
    return sessions


# --------------------------------------------------------------------------
# Atomic persistence / duplicate-writer prevention / resumability.
# --------------------------------------------------------------------------

def record_path(run_id: str) -> Path:
    return RESULTS_DIR / f"{run_id}.json"


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def acquire_lock(lock_path: Path = LOCK_PATH) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
    except FileExistsError:
        raise RuntimeError(
            f"Another R2-12 execution-driver process appears to hold the lock "
            f"({lock_path}). Refusing to start a second concurrent writer."
        )


def release_lock(lock_path: Path = LOCK_PATH) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


class DuplicateRunIdMismatch(RuntimeError):
    pass


def spec_identity_fields(spec: RunSpec) -> dict:
    return {
        "workload_id": spec.workload_id,
        "budget_gib": spec.budget_gib,
        "policy_id": spec.policy_id,
        "ordering_block": spec.ordering_block,
        "cell_index": spec.cell_index,
        "position_in_cell": spec.position_in_cell,
        "repetition_index": spec.repetition_index,
    }


def already_recorded(spec: RunSpec) -> bool:
    """True iff a record for this run_id already exists on disk AND its
    recorded configuration matches this spec exactly -- resumability skips
    these. A record that exists but does NOT match raises rather than
    being silently trusted or overwritten (duplicate-run-ID prevention)."""
    path = record_path(spec.run_id)
    if not path.exists():
        return False
    existing = load_json(path)
    expected = spec_identity_fields(spec)
    recorded = {k: existing.get(k) for k in expected}
    if recorded != expected:
        raise DuplicateRunIdMismatch(
            f"run_id {spec.run_id} already has a record on disk whose "
            f"configuration does not match the expected spec: "
            f"recorded={recorded} expected={expected}. Refusing to overwrite."
        )
    return True


def write_run_record(spec: RunSpec, result_fields: dict) -> None:
    """Writes a run record exactly once; a second attempt for the same
    run_id with a matching spec is a no-op (idempotent resume), and with a
    mismatched spec raises via already_recorded()."""
    if already_recorded(spec):
        return
    record = dict(spec_identity_fields(spec))
    record["run_id"] = spec.run_id
    record["policy_letter"] = spec.policy_letter
    record["budget_bytes"] = spec.budget_bytes
    record["written_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record.update(result_fields)
    atomic_write_json(record_path(spec.run_id), record)


# --------------------------------------------------------------------------
# Real execution is deliberately NOT implemented in this Phase-0 prep step.
# --------------------------------------------------------------------------

def _execute_cell_NOT_IMPLEMENTED(spec: RunSpec) -> dict:
    raise NotImplementedError(
        "Real R2-12 runtime execution is not implemented in this file by "
        "design: R2_12_RUNTIME_OPENED does not exist yet, and "
        "execution_plan.json.r2_12_runtime_opened_gate requires that "
        "separate, explicit commit to exist before ANY of the 144 process "
        "launches (warmups included) may begin. This Phase-0 preparation "
        "stops here."
    )


def run_session(budget_gib: float, *, mock: bool = False,
                 plan: Optional[List[RunSpec]] = None) -> dict:
    """Iterates one budget session's RunSpecs in frozen order. In --mock
    mode, writes SYNTHETIC placeholder records (no real or fabricated
    timing data) purely to exercise persistence/resumability. Outside
    --mock mode, every non-already-recorded run calls the NOT-IMPLEMENTED
    executor and raises -- this driver cannot produce a real result yet."""
    full_plan = plan if plan is not None else generate_full_plan()
    sessions = sessions_by_budget(full_plan)
    if budget_gib not in sessions:
        raise ValueError(f"no such budget session: {budget_gib}")

    acquire_lock()
    try:
        done, skipped = 0, 0
        for spec in sessions[budget_gib]:
            if already_recorded(spec):
                skipped += 1
                continue
            if mock:
                write_run_record(spec, {
                    "synthetic_test_record": True,
                    "real_execution": False,
                    "note": "mock mode: no real benchmark was executed; this record exists "
                            "only to exercise persistence/resumability logic.",
                })
                done += 1
            else:
                _execute_cell_NOT_IMPLEMENTED(spec)
        return {"budget_gib": budget_gib, "computed": done, "skipped_already_recorded": skipped,
                "total_in_session": len(sessions[budget_gib])}
    finally:
        release_lock()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", choices=["4.0", "1.5", "8.0", "all"], default="all")
    parser.add_argument(
        "--mock", action="store_true", default=False,
        help="Write SYNTHETIC placeholder records only, to exercise the "
             "persistence/resumability/session-partition machinery. Never "
             "produces or claims any real or fabricated scientific result.",
    )
    args = parser.parse_args()

    if not args.mock:
        print(
            "Real execution is not implemented in this file by design "
            "(R2_12_RUNTIME_OPENED does not exist). Re-run with --mock to "
            "exercise the plan/persistence/resumability machinery only.",
            file=sys.stderr,
        )
        return 2

    budgets = [4.0, 1.5, 8.0] if args.session == "all" else [float(args.session)]
    plan = generate_full_plan()
    for b in budgets:
        result = run_session(b, mock=True, plan=plan)
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
