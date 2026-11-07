"""R2-01B: independent Level-D behavioral evidence for R2-01.

Reuses Research Part 1's already-validated runtime-vs-replay fidelity
machinery (`argine.research.fidelity` / `rawlog` / `replay` /
`storereplay`) through its own EXP-021 closure tool,
`tools/validate_replay_fidelity.py`, rather than reimplementing any part
of the comparison. See `docs/research2/METHODOLOGY.md`, section "R2-01B:
closing the Level-D behavioral-observability gap", for the full
independence argument and the acceptance-criteria contract.

Level D asks a question Levels A-C cannot: does the REAL runtime's own
recorded cache behavior (a format-v2 `raw.tsv`'s hit/miss/load/evict
records, with an exact per-shard recorded mapping) agree with an
INDEPENDENT replay prediction (`StoreReplay`, fed only the runtime's own
request stream and its own recorded shard map -- never the route trace
information Levels A-C already validate)? Route information alone cannot
answer this, because replay already consumes it; that is why Level D
requires raw.tsv specifically, and fails closed to NOT_OBSERVABLE without
it.

This module never guesses a PASS or a FAIL: any missing file, a
format-v1 source (statistical shard mapping only -- insufficient for an
exact, non-circular, event-level comparison), or any exception while
parsing/evaluating returns NOT_OBSERVABLE with the reason recorded.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional

STATUS_NOT_OBSERVABLE = "NOT_OBSERVABLE"
STATUS_OBSERVED_PASS = "OBSERVED_PASS"
STATUS_OBSERVED_FAIL = "OBSERVED_FAIL"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "validate_replay_fidelity.py"
_MODULE_NAME = "argine_research2_r2_01_validate_replay_fidelity"


def _load_tool():
    """Import tools/validate_replay_fidelity.py by file path (it is a
    script, not a package member) so `one()` and its predeclared
    `WORKLOAD` criterion tuple are reused verbatim -- never duplicated,
    never re-derived, never silently allowed to drift from what Research
    Part 1 uses for its own EXP-021 closure."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _TOOL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _not_observable(reason: str) -> Dict[str, Any]:
    return {"status": STATUS_NOT_OBSERVABLE, "reason": reason, "metrics": None}


def evaluate(run_dir: "str | Path", *, pin_model: str = "per-thread",
             seeds: int = 1) -> Dict[str, Any]:
    """Evaluate Level-D behavioral evidence for one capture run directory
    (the directory containing `trace.jsonl.gz` -- i.e. the resolved R2-01
    trace source's own parent directory).

    `seeds` only matters for a format-v1 source (statistical hash-based
    shard-map ensemble); for format v2 -- the only case this function ever
    returns OBSERVED_PASS/OBSERVED_FAIL for -- the recorded shard map is
    exact and `seeds` has no effect. Default 1 keeps a v1 call (which
    always returns NOT_OBSERVABLE below) cheap regardless.

    Never raises: any problem reading or evaluating the telemetry is
    reported as NOT_OBSERVABLE with a specific reason, so a malformed or
    partial raw.tsv can never be silently promoted to a PASS or a FAIL.
    """
    run_dir = Path(run_dir)
    raw_path = run_dir / "raw.tsv"
    trace_path = run_dir / "trace.jsonl.gz"
    markers_path = run_dir / "markers.jsonl"

    if not raw_path.is_file():
        return _not_observable(
            f"no raw.tsv next to this trace source ({run_dir}): no independently "
            f"recorded runtime cache events are available for this source")
    if not trace_path.is_file():
        return _not_observable(f"raw.tsv present but trace.jsonl.gz missing in {run_dir}")
    if not markers_path.is_file():
        return _not_observable(f"raw.tsv present but markers.jsonl missing in {run_dir}")

    try:
        tool = _load_tool()
    except Exception as e:  # noqa: BLE001 -- fail closed, never crash the run
        return _not_observable(f"could not load Research Part 1 fidelity tooling: {e}")

    try:
        row, detail = tool.one(run_dir, seeds, pin_model)
    except Exception as e:  # noqa: BLE001 -- malformed/partial telemetry fails closed
        return _not_observable(f"behavioral fidelity evaluation failed: {e}")

    fmt = row.get("format")
    if fmt is None or fmt < 2:
        return _not_observable(
            f"raw.tsv is instrumentation format v{fmt}: it lacks the exact per-shard "
            f"recorded mapping and sequence numbers needed for a non-statistical, "
            f"event-level runtime-vs-replay comparison (only a statistical hash-based "
            f"ensemble is possible, which this contract does not accept as independent "
            f"behavioral evidence)")

    closure = row.get("exp021_closure", "")
    metrics = {
        "run": row.get("run"),
        "format": fmt,
        "shards": row.get("shards"),
        "shard_map": row.get("shard_map"),
        "reconstruct_inconsistencies": row.get("reconstruct_inconsistencies"),
        "shards_with_replay_relevant_seq_gaps": row.get("shards_with_replay_relevant_seq_gaps"),
        "incomplete_nodes_in_workload": row.get("incomplete_nodes_in_workload"),
        "event_disagreement_rate": row.get("event_disagreement_rate"),
        "wl_disagreements": row.get("wl_disagreements"),
        "wl_victim_disagreements": row.get("wl_victim_disagreements"),
        "wl_skip_count_disagreements": row.get("wl_skip_count_disagreements"),
        "wl_rt_misses": row.get("wl_rt_misses"), "wl_rp_misses": row.get("wl_rp_misses"),
        "wl_rt_miss_bytes": row.get("wl_rt_miss_bytes"),
        "wl_rp_miss_bytes": row.get("wl_rp_miss_bytes"),
        "wl_rt_evictions": row.get("wl_rt_evictions"),
        "wl_rp_evictions": row.get("wl_rp_evictions"),
        "rp_budget_violations": row.get("rp_budget_violations"),
        "wl_rp_failures": row.get("wl_rp_failures"),
        "pre_workload_disagreements": row.get("pre_workload_disagreements"),
        "pre_workload_skip_count_disagreements": row.get("pre_workload_skip_count_disagreements"),
        "pre_workload_rp_failures": row.get("pre_workload_rp_failures"),
        "tail_disagreements_unexplained": row.get("tail_disagreements_unexplained"),
        "workload_fidelity": row.get("workload_fidelity"),
        "full_trace_fidelity": row.get("full_trace_fidelity"),
        "termination_tail_completeness": row.get("termination_tail_completeness"),
        "exp021_closure": closure,
    }

    if closure == "PASS":
        return {"status": STATUS_OBSERVED_PASS,
                "reason": ("real runtime cache events (raw.tsv, format v2, exact recorded "
                           "shard map) agree with independent StoreReplay prediction with "
                           "zero disagreements over the measured workload and zero "
                           "unexplained tail disagreement"),
                "metrics": metrics}
    return {"status": STATUS_OBSERVED_FAIL,
            "reason": f"EXP-021 closure criteria not met: {closure}",
            "metrics": metrics}
