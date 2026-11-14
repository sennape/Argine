"""Tests for the R2-12 execution-driver PREPARATION logic only.

These tests exercise matrix generation, session partitioning, atomic
persistence, duplicate-writer prevention, and resumability. They never
produce, read, or claim any real R2-12 scientific result -- all
persistence tests use --mock-equivalent SYNTHETIC records in an isolated
scratch directory, never the repository's own experiments/ tree.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.r2_12_execution_driver as drv  # noqa: E402


@pytest.fixture()
def scratch_driver(tmp_path, monkeypatch):
    """Reloads the module with RESULTS_DIR/LOCK_PATH redirected into a
    pytest tmp_path, so persistence tests never touch the real repo tree,
    and restores the original module state afterward."""
    orig_results_dir = drv.RESULTS_DIR
    orig_lock_path = drv.LOCK_PATH
    drv.RESULTS_DIR = tmp_path / "runtime_results"
    drv.LOCK_PATH = tmp_path / ".lock"
    try:
        yield drv
    finally:
        drv.RESULTS_DIR = orig_results_dir
        drv.LOCK_PATH = orig_lock_path


def test_full_plan_has_144_runs_with_correct_split():
    plan = drv.generate_full_plan()
    assert len(plan) == 144
    assert sum(1 for r in plan if r.ordering_block == "warmup") == 24
    assert sum(1 for r in plan if r.ordering_block == "counted") == 120


def test_all_run_ids_unique():
    plan = drv.generate_full_plan()
    ids = [r.run_id for r in plan]
    assert len(set(ids)) == len(ids) == 144


def test_plan_generation_is_deterministic():
    plan1 = drv.generate_full_plan()
    plan2 = drv.generate_full_plan()
    assert [r.run_id for r in plan1] == [r.run_id for r in plan2]


def test_exact_40_40_40_budget_partition():
    plan = drv.generate_full_plan()
    sessions = drv.sessions_by_budget(plan)
    assert set(sessions.keys()) == {4.0, 1.5, 8.0}
    for budget_gib, specs in sessions.items():
        counted = sum(1 for s in specs if s.ordering_block == "counted")
        warmups = sum(1 for s in specs if s.ordering_block == "warmup")
        assert counted == 40, budget_gib
        assert warmups == 8, budget_gib
        assert len(specs) == 48, budget_gib


def test_sessions_preserve_frozen_relative_order():
    plan = drv.generate_full_plan()
    sessions = drv.sessions_by_budget(plan)
    for budget_gib, specs in sessions.items():
        cell_indices = [s.cell_index for s in specs if s.ordering_block == "warmup"][::2]
        assert cell_indices == sorted(cell_indices)


def test_each_cell_has_opener_warmup_before_other_warmup_before_sequence():
    plan = drv.generate_full_plan()
    by_cell = {}
    for r in plan:
        by_cell.setdefault(r.cell_index, []).append(r)
    run_order = drv.load_json(drv.CONTRACT_DIR / "run_order.json")
    table = {c["cell_index"]: c for c in run_order["cell_sequence_table"]}
    for cell_index, runs in by_cell.items():
        runs = sorted(runs, key=lambda r: r.position_in_cell)
        assert len(runs) == 12
        opener_letter = table[cell_index]["opening_policy"]
        assert runs[0].ordering_block == "warmup" and runs[0].policy_letter == opener_letter
        assert runs[1].ordering_block == "warmup" and runs[1].policy_letter != opener_letter
        counted_letters = [r.policy_letter for r in runs[2:]]
        assert counted_letters == table[cell_index]["sequence"]


def test_repetition_indices_count_1_through_5_per_policy_per_cell():
    plan = drv.generate_full_plan()
    by_cell = {}
    for r in plan:
        if r.ordering_block == "counted":
            by_cell.setdefault((r.cell_index, r.policy_letter), []).append(r.repetition_index)
    for key, reps in by_cell.items():
        assert sorted(reps) == [1, 2, 3, 4, 5], key


def test_frozen_run_order_loads_without_error():
    # "frozen contract loading" -- re-verifies the files this module reads
    # from parse cleanly and are internally consistent with repetition_plan.
    run_order = drv.load_json(drv.CONTRACT_DIR / "run_order.json")
    repetition_plan = drv.load_json(drv.CONTRACT_DIR / "repetition_plan.json")
    assert len(run_order["cell_sequence_table"]) == 12
    assert repetition_plan["grand_total_process_launches"] == 144


def test_mock_mode_writes_synthetic_tagged_records_only(scratch_driver):
    plan = scratch_driver.generate_full_plan()
    result = scratch_driver.run_session(4.0, mock=True, plan=plan)
    assert result["computed"] == 48
    assert result["skipped_already_recorded"] == 0

    sessions = scratch_driver.sessions_by_budget(plan)
    for spec in sessions[4.0]:
        rec = json.loads(scratch_driver.record_path(spec.run_id).read_text())
        assert rec["synthetic_test_record"] is True
        assert rec["real_execution"] is False


def test_resumability_skips_already_recorded_runs(scratch_driver):
    plan = scratch_driver.generate_full_plan()
    r1 = scratch_driver.run_session(1.5, mock=True, plan=plan)
    assert r1["computed"] == 48
    r2 = scratch_driver.run_session(1.5, mock=True, plan=plan)
    assert r2["computed"] == 0
    assert r2["skipped_already_recorded"] == 48


def test_mismatched_existing_record_raises_not_silently_overwritten(scratch_driver):
    plan = scratch_driver.generate_full_plan()
    scratch_driver.run_session(8.0, mock=True, plan=plan)
    sessions = scratch_driver.sessions_by_budget(plan)
    victim = sessions[8.0][0]
    path = scratch_driver.record_path(victim.run_id)
    rec = json.loads(path.read_text())
    rec["workload_id"] = "TAMPERED-DOES-NOT-MATCH"
    path.write_text(json.dumps(rec))

    with pytest.raises(scratch_driver.DuplicateRunIdMismatch):
        scratch_driver.already_recorded(victim)


def test_duplicate_writer_prevention_via_lock(scratch_driver):
    scratch_driver.acquire_lock()
    try:
        with pytest.raises(RuntimeError):
            scratch_driver.acquire_lock()
    finally:
        scratch_driver.release_lock()
    # lock released cleanly -- re-acquiring now must succeed
    scratch_driver.acquire_lock()
    scratch_driver.release_lock()


def test_no_benchmark_execution_from_non_mock_mode(scratch_driver):
    plan = scratch_driver.generate_full_plan()
    with pytest.raises(NotImplementedError):
        scratch_driver.run_session(4.0, mock=False, plan=plan)
    # and nothing was written as a side effect of the attempt
    assert not scratch_driver.RESULTS_DIR.exists() or not any(scratch_driver.RESULTS_DIR.iterdir())


def test_execute_cell_not_implemented_always_raises():
    plan = drv.generate_full_plan()
    with pytest.raises(NotImplementedError):
        drv._execute_cell_NOT_IMPLEMENTED(plan[0])


def test_cli_without_mock_flag_refuses_and_does_not_execute(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["r2_12_execution_driver.py"])
    rc = drv.main()
    assert rc == 2
    captured = capsys.readouterr()
    assert "not implemented" in captured.err.lower()


# --- Lock-path late-binding fix regression tests ---
#
# acquire_lock()/release_lock() originally took `lock_path: Path = LOCK_PATH`
# as a default parameter, which Python evaluates ONCE at module-import time.
# Reassigning the module-level `drv.LOCK_PATH` attribute afterward (as a test
# fixture, or future tooling, might do to redirect the lock into an isolated
# directory) silently had NO effect on calls made with no explicit argument --
# they kept using whatever LOCK_PATH equaled at import time. This caused a
# real stray lock file at the true repo path during this session's own
# testing. The fix resolves the module-level LOCK_PATH fresh on every call
# via a `None` sentinel. These tests guard against regressing back to the
# bound-default pattern.

def test_module_level_lock_path_reassignment_is_respected_by_acquire_release(tmp_path):
    original_lock_path = drv.LOCK_PATH
    try:
        drv.LOCK_PATH = tmp_path / ".reassigned.lock"
        drv.acquire_lock()
        assert drv.LOCK_PATH.exists()
        # the ORIGINAL default path must NOT have been touched
        assert not original_lock_path.exists()
        drv.release_lock()
        assert not drv.LOCK_PATH.exists()
    finally:
        drv.LOCK_PATH = original_lock_path


def test_explicit_lock_path_override_works_independent_of_module_attribute(tmp_path):
    explicit_path = tmp_path / ".explicit.lock"
    drv.acquire_lock(explicit_path)
    assert explicit_path.exists()
    drv.release_lock(explicit_path)
    assert not explicit_path.exists()


def test_acquire_and_release_use_the_exact_same_resolved_path(tmp_path, monkeypatch):
    target = tmp_path / ".symmetric.lock"
    monkeypatch.setattr(drv, "LOCK_PATH", target)
    drv.acquire_lock()
    assert target.exists()
    # release with NO argument must resolve to the SAME path acquire used
    drv.release_lock()
    assert not target.exists()


def test_duplicate_writer_still_detected_after_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(drv, "LOCK_PATH", tmp_path / ".dup.lock")
    drv.acquire_lock()
    try:
        with pytest.raises(RuntimeError):
            drv.acquire_lock()
    finally:
        drv.release_lock()


def test_no_lock_residue_after_run_session_normal_completion(scratch_driver):
    plan = scratch_driver.generate_full_plan()
    scratch_driver.run_session(1.5, mock=True, plan=plan)
    assert not scratch_driver.LOCK_PATH.exists()


def test_no_lock_residue_after_run_session_raises(scratch_driver):
    plan = scratch_driver.generate_full_plan()
    with pytest.raises(NotImplementedError):
        scratch_driver.run_session(4.0, mock=False, plan=plan)
    assert not scratch_driver.LOCK_PATH.exists()


def test_run_session_acquire_and_release_agree_on_reassigned_path(scratch_driver):
    # Regression for the exact bug: run_session's internal acquire/release
    # must both honor the scratch_driver fixture's LOCK_PATH reassignment,
    # never falling back to the real repo-level default.
    plan = scratch_driver.generate_full_plan()
    scratch_driver.run_session(8.0, mock=True, plan=plan)
    real_default_lock = drv.REPO_ROOT / "tools" / ".r2_12_execution_driver.lock"
    assert not real_default_lock.exists(), (
        "run_session must never touch the real repo-level lock path when "
        "LOCK_PATH has been reassigned"
    )


def test_acquire_lock_docstrings_do_not_reintroduce_bound_default():
    import inspect
    sig_acquire = inspect.signature(drv.acquire_lock)
    sig_release = inspect.signature(drv.release_lock)
    assert sig_acquire.parameters["lock_path"].default is None
    assert sig_release.parameters["lock_path"].default is None
