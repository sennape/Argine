"""R2-10 Phase-1: resumable DEV tournament runner architecture.

Mirrors the checkpointing lessons from the R2-09A 624-cell tournament
(argine/research2/r2_09a/checkpoint_runner.py): per-cell checkpointing,
atomic (tmp-then-replace, Windows-safe retry) writes, a manifest that
tracks completed/pending counts and an execution history, fail-closed
validation of any existing checkpoint cell, and no full-tournament state
held in memory across cells.

This module provides the ARCHITECTURE and is exercised by a Phase-1 smoke
test on a tiny, explicitly non-selection subset (see
tests/test_r2_10_runner_smoke.py). No full DEV tournament has been run
with it. It never reads experiments/R2-09-prediction-benchmark/eval_manifest.json
and never regenerates the frozen R2-09A dev_corpus_manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...research.trace import Trace
from .causal_walk import iter_causal_contexts, same_layer_stream, target_at
from .contract import R2_09A_DEV_CORPUS_DIGEST, assert_valid, r2_09a_dir
from .output_contract import recall_at_k
from .predictors import all_configs as predictor_all_configs

CHECKPOINT_SCHEMA_VERSION = 1
RUNNER_VERSION = "r2-10-phase1-checkpoint-runner-v1"
HORIZONS = (1, 4, 16)
PRIMARY_HORIZON = 1


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()


def cell_key(dev_cell_id: str, family_id: str, config_id: str) -> str:
    """Unique canonical scientific identity for one R2-10 DEV cell."""
    raw = f"{dev_cell_id}|{family_id}|{config_id}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    safe = f"{dev_cell_id}__{family_id}__{config_id}".replace("/", "_").replace("=", "-").replace(",", "_").replace(".", "p")
    return safe[:180] + "__" + h


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    data = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    last_err = None
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:  # Windows sharing-violation retry
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    raise last_err


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ConcurrentWriterError(RuntimeError):
    pass


class TournamentLock:
    """Exclusive-create lock file preventing concurrent writers to the
    same checkpoint directory. Stale locks (dead pid) are detected via
    psutil when available; otherwise a lock is only ever removed by the
    process that created it."""

    def __init__(self, ckpt_dir: Path):
        self.path = ckpt_dir / ".lock"
        self._acquired = False

    def __enter__(self) -> "TournamentLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                info = load_json(self.path)
                other_pid = info.get("pid")
            except Exception:
                other_pid = None
            if other_pid is not None and _pid_alive(other_pid):
                raise ConcurrentWriterError(f"tournament directory locked by live pid {other_pid}: {self.path}")
            # stale lock (process no longer alive, or unreadable) -- safe to reclaim
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "acquired_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
        self._acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._acquired and self.path.exists():
            try:
                os.remove(self.path)
            except OSError:
                pass


def _pid_alive(pid: int) -> bool:
    try:
        import psutil  # type: ignore

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True  # unknown platform behavior -- be conservative, assume live


def validate_runner_env() -> Dict[str, Any]:
    """Fail closed if the R2-10 Phase-0 contract or the R2-09A DEV corpus
    it binds to have drifted from their pinned digests."""
    v = assert_valid()  # raises ContractValidationError if contract invalid/digest mismatched
    dev_manifest = load_json(r2_09a_dir() / "dev_corpus_manifest.json")
    if dev_manifest.get("dev_corpus_digest") != R2_09A_DEV_CORPUS_DIGEST:
        raise RuntimeError("dev_corpus_manifest.json digest drifted from pinned R2_09A_DEV_CORPUS_DIGEST")
    return {"phase0_contract_digest": v["digest"], "dev_corpus_digest": R2_09A_DEV_CORPUS_DIGEST, "dev_manifest": dev_manifest}


def expected_cells_for_dev_subset(dev_cell_ids: Sequence[str], n_experts: int) -> List[Tuple[str, str, str]]:
    """(dev_cell_id, family_id, config_id) triples for the given DEV cell
    subset, across the full frozen predictor config grid (all 16
    configurations: 1+1+4+1+3+6 across the six families)."""
    configs = predictor_all_configs(n_experts)
    out: List[Tuple[str, str, str]] = []
    from .baselines import GlobalFrequencyBaseline, LastObservationBaseline

    full: Dict[str, List] = {
        "global_frequency": [GlobalFrequencyBaseline(n_experts)],
        "last_observation": [LastObservationBaseline(n_experts)],
        **configs,
    }
    for dev_cell_id in dev_cell_ids:
        for family_id, preds in full.items():
            for p in preds:
                out.append((dev_cell_id, family_id, p.config_id))
    return out, full  # type: ignore[return-value]


def run_predictor_against_trace(trace: Trace, predictor, horizons: Sequence[int] = HORIZONS) -> Dict[str, Any]:
    """Aggregate recall@k for `predictor` across every layer of `trace`,
    for every horizon, with equal weight per (t,l) target within this one
    DEV cell (selection_rule.json aggregation). Also enforces the
    cardinality-availability fail-safe (CARDINALITY-AVAILABILITY-AUDIT-001):
    raises if any observed target cardinality != trace.meta.top_k.
    """
    top_k = trace.meta.top_k
    n_experts = trace.meta.n_experts
    sums: Dict[int, float] = {h: 0.0 for h in horizons}
    counts: Dict[int, int] = {h: 0 for h in horizons}
    total_targets_seen = 0
    for layer in trace.meta.layers:
        stream = same_layer_stream(trace, layer, phase="decode")
        if not stream:
            continue
        contexts = list(iter_causal_contexts(stream, k=top_k))
        for ctx in contexts:
            pred = predictor.predict(ctx)  # uses ctx.k == top_k, never the target
            for h in horizons:
                tgt_entry = target_at(stream, ctx.position, h)
                if tgt_entry is None:
                    continue  # unavailable near trace end -- skip, never fabricate
                target = tgt_entry[1]
                if len(target) != top_k:
                    raise RuntimeError(
                        f"cardinality fail-safe triggered: target cardinality {len(target)} != "
                        f"trace.meta.top_k {top_k} at layer {layer} position {ctx.position} "
                        f"(see CARDINALITY-AVAILABILITY-AUDIT-001)"
                    )
                total_targets_seen += 1
                sums[h] += recall_at_k(pred, target)
                counts[h] += 1
    means = {f"recall_at_k_h{h}": (sums[h] / counts[h] if counts[h] else None) for h in horizons}
    return {
        "n_experts": n_experts,
        "top_k": top_k,
        "layers": len(trace.meta.layers),
        "targets_scored": {f"h{h}": counts[h] for h in horizons},
        **means,
    }


def validate_completed_cell_record(rec: Dict[str, Any], expected_identity: Dict[str, str]) -> Tuple[bool, str]:
    for k in ("dev_cell_id", "family_id", "config_id", "cell_key"):
        if rec.get(k) != expected_identity.get(k):
            return False, f"identity mismatch {k}"
    if rec.get("status") != "OK" or not rec.get("deterministic"):
        return False, "cell not complete/valid"
    req = ("recall_at_k_h1", "recall_at_k_h4", "recall_at_k_h16", "result_digest")
    for k in req:
        if k not in rec:
            return False, f"missing {k}"
    check_obj = {k: rec[k] for k in ("dev_cell_id", "family_id", "config_id", "recall_at_k_h1",
                                      "recall_at_k_h4", "recall_at_k_h16", "result_digest", "deterministic", "status")}
    if rec.get("cell_record_digest") != _digest(check_obj):
        return False, "cell_record_digest mismatch"
    return True, "ok"


def run_checkpointed(
    dev_cell_ids: Sequence[str],
    ckpt_dir: Path,
    trace_loader,
    n_experts: int = 128,
    progress_every: int = 5,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run (or resume) the R2-10 tournament over `dev_cell_ids` using
    `trace_loader(dev_cell_id) -> Trace`. One trace is held in memory at a
    time (freed after its cells complete), never the whole tournament.
    """
    env = validate_runner_env()
    expected_cells, full_configs = expected_cells_for_dev_subset(dev_cell_ids, n_experts)
    configs_digest = _digest(sorted([{"family_id": f, "config_id": c} for _, f, c in expected_cells], key=lambda x: (x["family_id"], x["config_id"])))
    cells_dir = ckpt_dir / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = ckpt_dir / "manifest.json"
    expected_count = len(expected_cells)

    with TournamentLock(ckpt_dir):
        if manifest_path.exists():
            m = load_json(manifest_path)
            if m.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                raise RuntimeError("checkpoint schema mismatch")
            if m.get("phase0_contract_digest") != env["phase0_contract_digest"]:
                raise RuntimeError("checkpoint contract digest mismatch")
            if m.get("dev_corpus_digest") != env["dev_corpus_digest"]:
                raise RuntimeError("checkpoint corpus digest mismatch")
            if m.get("configs_digest") != configs_digest:
                raise RuntimeError("checkpoint configs digest mismatch (grid changed?)")
            if m.get("expected_cell_count") != expected_count:
                raise RuntimeError("checkpoint expected cell count mismatch")
        else:
            m = {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "runner_version": RUNNER_VERSION,
                "execution_generation_id": uuid.uuid4().hex,
                "creation_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_update_utc": None,
                "phase0_contract_digest": env["phase0_contract_digest"],
                "dev_corpus_digest": env["dev_corpus_digest"],
                "configs_digest": configs_digest,
                "expected_cell_count": expected_count,
                "completed_cell_count": 0,
                "pending_cell_count": expected_count,
                "execution_history": [],
            }
            atomic_write_json(manifest_path, m)

        completed: Dict[str, Dict[str, Any]] = {}
        for dev_cell_id, family_id, config_id in expected_cells:
            ck = cell_key(dev_cell_id, family_id, config_id)
            p = cells_dir / f"{ck}.json"
            if not p.exists():
                continue
            rec = load_json(p)
            ok, reason = validate_completed_cell_record(
                rec, {"dev_cell_id": dev_cell_id, "family_id": family_id, "config_id": config_id, "cell_key": ck}
            )
            if not ok:
                raise RuntimeError(f"fail closed: invalid checkpoint cell {p}: {reason}")
            completed[ck] = rec

        pending = [
            (dev_cell_id, family_id, config_id)
            for dev_cell_id, family_id, config_id in expected_cells
            if cell_key(dev_cell_id, family_id, config_id) not in completed
        ]
        m["completed_cell_count"] = len(completed)
        m["pending_cell_count"] = len(pending)
        m["last_update_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        m.setdefault("execution_history", []).append(
            {"event": "resume_or_start", "pid": os.getpid(), "utc": m["last_update_utc"],
             "completed": len(completed), "pending": len(pending)}
        )
        atomic_write_json(manifest_path, m)

        # Process one DEV cell (one trace) at a time; never hold more than
        # one trace and never the whole tournament's results in memory.
        pending_by_cell: Dict[str, List[Tuple[str, str]]] = {}
        for dev_cell_id, family_id, config_id in pending:
            pending_by_cell.setdefault(dev_cell_id, []).append((family_id, config_id))

        done = len(completed)
        for dev_cell_id, fam_cfgs in pending_by_cell.items():
            trace = trace_loader(dev_cell_id)
            for family_id, config_id in fam_cfgs:
                predictor = next(p for p in full_configs[family_id] if p.config_id == config_id)
                ck = cell_key(dev_cell_id, family_id, config_id)
                r1 = run_predictor_against_trace(trace, predictor)
                r2 = run_predictor_against_trace(trace, predictor)
                if r1 != r2:
                    raise RuntimeError(f"FAILED_NONDETERMINISTIC {ck}")
                rec = {
                    "cell_key": ck,
                    "dev_cell_id": dev_cell_id,
                    "family_id": family_id,
                    "config_id": config_id,
                    "recall_at_k_h1": r1["recall_at_k_h1"],
                    "recall_at_k_h4": r1["recall_at_k_h4"],
                    "recall_at_k_h16": r1["recall_at_k_h16"],
                    "targets_scored": r1["targets_scored"],
                    "result_digest": _digest(r1),
                    "deterministic": True,
                    "status": "OK",
                }
                check_obj = {k: rec[k] for k in ("dev_cell_id", "family_id", "config_id", "recall_at_k_h1",
                                                  "recall_at_k_h4", "recall_at_k_h16", "result_digest",
                                                  "deterministic", "status")}
                rec["cell_record_digest"] = _digest(check_obj)
                atomic_write_json(cells_dir / f"{ck}.json", rec)
                completed[ck] = rec
                done += 1
                if done % progress_every == 0 or done == expected_count:
                    m["completed_cell_count"] = len(completed)
                    m["pending_cell_count"] = expected_count - len(completed)
                    m["last_update_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    atomic_write_json(manifest_path, m)
            del trace  # free before loading the next DEV cell's trace

        m["completed_cell_count"] = len(completed)
        m["pending_cell_count"] = 0
        m["last_update_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        m.setdefault("execution_history", []).append(
            {"event": "complete", "utc": m["last_update_utc"], "completed": len(completed)}
        )
        atomic_write_json(manifest_path, m)

    rows = [completed[cell_key(d, f, c)] for d, f, c in expected_cells]
    return rows, m
