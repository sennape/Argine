"""R2-10 Phase-1 Step-6 smoke validation: NOT a selection run, NOT final
scientific evidence. Validates only that the runner architecture works:
execution, valid cells, correct metrics, checkpoint/resume, interruption
survival, determinism, and that no frozen artifact is touched.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from argine.research.trace import Step, Trace, TraceMeta
from argine.research2.r2_10 import runner as r2_10_runner
from argine.research2.r2_10.contract import compute_contract_digest, default_contract_dir

N_EXPERTS = 8
TOP_K = 3
LAYERS = [0, 1]


def _synthetic_trace(seed: int, length: int = 30) -> Trace:
    import random

    rng = random.Random(seed)
    meta = TraceMeta(model="synthetic-smoke", n_experts=N_EXPERTS, top_k=TOP_K, layers=LAYERS)
    steps = []
    for i in range(length):
        routes = {l: [tuple(sorted(rng.sample(range(N_EXPERTS), TOP_K)))] for l in LAYERS}
        steps.append(Step(graph=i, n_tokens=1, phase="decode", routes=routes))
    return Trace(meta=meta, steps=steps)


SMOKE_CELL_IDS = ["smoke-cell-A", "smoke-cell-B"]
SMOKE_TRACES = {cid: _synthetic_trace(seed=i) for i, cid in enumerate(SMOKE_CELL_IDS)}


def _loader(cell_id: str) -> Trace:
    return SMOKE_TRACES[cell_id]


def test_contract_digest_unchanged_before_smoke_run():
    # Baseline capture, re-asserted after the smoke run below.
    assert compute_contract_digest() == "4d2288b7b4a812fd85d96aa7c2227d639b52f5131bff305d687e03970f53d725"


def test_smoke_run_executes_all_predictors_and_produces_valid_cells():
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_dir = Path(tmp) / "smoke_ckpt"
        rows, manifest = r2_10_runner.run_checkpointed(
            SMOKE_CELL_IDS, ckpt_dir, _loader, n_experts=N_EXPERTS, progress_every=3
        )
        expected_n = len(SMOKE_CELL_IDS) * 16  # 1+1+4+1+3+6 configs across 6 families
        assert len(rows) == expected_n
        assert manifest["completed_cell_count"] == expected_n
        assert manifest["pending_cell_count"] == 0
        for rec in rows:
            assert rec["status"] == "OK"
            assert rec["deterministic"] is True
            assert rec["recall_at_k_h1"] is not None
            assert 0.0 <= rec["recall_at_k_h1"] <= 1.0
            assert rec["targets_scored"]["h1"] > 0


def test_checkpoint_resume_skips_already_completed_cells():
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_dir = Path(tmp) / "smoke_ckpt"
        rows1, m1 = r2_10_runner.run_checkpointed(
            SMOKE_CELL_IDS, ckpt_dir, _loader, n_experts=N_EXPERTS, progress_every=3
        )
        # Resume: calling again with everything already done must not
        # recompute (same manifest generation id, same cell records).
        gen1 = m1["execution_generation_id"]
        rows2, m2 = r2_10_runner.run_checkpointed(
            SMOKE_CELL_IDS, ckpt_dir, _loader, n_experts=N_EXPERTS, progress_every=3
        )
        assert m2["execution_generation_id"] == gen1
        assert rows1 == rows2
        assert m2["completed_cell_count"] == m1["completed_cell_count"]


def test_interrupted_execution_preserves_completed_cells_and_resumes():
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_dir = Path(tmp) / "smoke_ckpt"

        call_count = {"n": 0}
        real_run = r2_10_runner.run_predictor_against_trace

        def flaky_run(trace, predictor, horizons=r2_10_runner.HORIZONS):
            call_count["n"] += 1
            if call_count["n"] == 5:
                raise RuntimeError("simulated interruption")
            return real_run(trace, predictor, horizons)

        r2_10_runner.run_predictor_against_trace = flaky_run
        try:
            with pytest.raises(RuntimeError, match="simulated interruption"):
                r2_10_runner.run_checkpointed(
                    SMOKE_CELL_IDS, ckpt_dir, _loader, n_experts=N_EXPERTS, progress_every=1
                )
        finally:
            r2_10_runner.run_predictor_against_trace = real_run

        # Completed cells before the interruption must be on disk and valid.
        manifest_path = ckpt_dir / "manifest.json"
        assert manifest_path.exists()
        cells_dir = ckpt_dir / "cells"
        n_before = len(list(cells_dir.glob("*.json")))
        assert n_before > 0

        # Lock must have been released (TournamentLock.__exit__ runs even on
        # exception) so resume is not blocked by a stale lock.
        assert not (ckpt_dir / ".lock").exists()

        rows, manifest = r2_10_runner.run_checkpointed(
            SMOKE_CELL_IDS, ckpt_dir, _loader, n_experts=N_EXPERTS, progress_every=3
        )
        expected_n = len(SMOKE_CELL_IDS) * 16
        assert len(rows) == expected_n
        assert manifest["completed_cell_count"] == expected_n


def test_repeated_execution_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        rows1, _ = r2_10_runner.run_checkpointed(
            SMOKE_CELL_IDS, Path(tmp1) / "ckpt", _loader, n_experts=N_EXPERTS
        )
        rows2, _ = r2_10_runner.run_checkpointed(
            SMOKE_CELL_IDS, Path(tmp2) / "ckpt", _loader, n_experts=N_EXPERTS
        )
        by_key1 = {r["cell_key"]: r for r in rows1}
        by_key2 = {r["cell_key"]: r for r in rows2}
        assert by_key1.keys() == by_key2.keys()
        for k in by_key1:
            assert by_key1[k]["recall_at_k_h1"] == by_key2[k]["recall_at_k_h1"]
            assert by_key1[k]["recall_at_k_h4"] == by_key2[k]["recall_at_k_h4"]
            assert by_key1[k]["recall_at_k_h16"] == by_key2[k]["recall_at_k_h16"]


def test_concurrent_writer_prevention():
    with tempfile.TemporaryDirectory() as tmp:
        ckpt_dir = Path(tmp) / "smoke_ckpt"
        ckpt_dir.mkdir(parents=True)
        with r2_10_runner.TournamentLock(ckpt_dir):
            with pytest.raises(r2_10_runner.ConcurrentWriterError):
                with r2_10_runner.TournamentLock(ckpt_dir):
                    pass


def test_cardinality_failsafe_triggers_on_off_contract_trace():
    # A trace where one layer's selection has the WRONG cardinality
    # relative to meta.top_k must raise, not silently mis-score.
    meta = TraceMeta(model="bad", n_experts=N_EXPERTS, top_k=TOP_K, layers=[0])
    steps = [
        Step(graph=0, n_tokens=1, phase="decode", routes={0: [(0, 1, 2)]}),
        Step(graph=1, n_tokens=1, phase="decode", routes={0: [(3, 4)]}),  # wrong cardinality (2 != top_k=3)
        Step(graph=2, n_tokens=1, phase="decode", routes={0: [(0, 1, 2)]}),
    ]
    bad_trace = Trace(meta=meta, steps=steps)
    from argine.research2.r2_10.baselines import GlobalFrequencyBaseline

    with pytest.raises(RuntimeError, match="cardinality fail-safe"):
        r2_10_runner.run_predictor_against_trace(bad_trace, GlobalFrequencyBaseline(N_EXPERTS))


def test_no_frozen_artifact_modified_by_smoke_run():
    before = compute_contract_digest()
    with tempfile.TemporaryDirectory() as tmp:
        r2_10_runner.run_checkpointed(SMOKE_CELL_IDS, Path(tmp) / "ckpt", _loader, n_experts=N_EXPERTS)
    after = compute_contract_digest()
    assert before == after == "4d2288b7b4a812fd85d96aa7c2227d639b52f5131bff305d687e03970f53d725"
    # No file under the frozen contract dir changed mtime/content as a side effect.
    for f in default_contract_dir().glob("*.json"):
        json.loads(f.read_text(encoding="utf-8"))  # still valid JSON, untouched
