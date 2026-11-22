"""R3-01 telemetry realization: record builders, pairing, admission.

Infrastructure only. This module never runs the model, never opens EVAL and
never changes runtime behaviour. It turns an existing retention-decision trace
into a contract-shaped TRACE_COUNT record, builds TIMING records, pairs them
into a scientific cell, and admits or rejects the result against the frozen
R3-00A contract.

The frozen R3-00A validator is imported and used as-is; this module adds only
the R3-01 layer (record schema, output-token digest semantics, trace archival,
pair construction, and the X-series admission rules).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE_DIR = Path(__file__).resolve().parents[1]
R3A_DIR = REPO_ROOT / "experiments" / "R3-00A-observability-contract"

# The frozen contract must be imported, never modified.
sys.path.insert(0, str(R3A_DIR / "validator"))
import telemetry_contract as frozen  # noqa: E402

CONTRACT_PATH = R3A_DIR / "OBSERVABILITY_CONTRACT.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CONTRACT_DIGEST = "99a68c73c5a3caba4fd53937f27907375734f3866c84e18d4ecb944013efa073"
SCHEMA_VERSION = 1
OUTPUT_DIGEST_PREFIX = "argine-output-tokens-v1\n"
TRACE_FORMAT_V1 = "argine-retention-decision-trace/v1"

# Traces larger than this are never copied into Git: only referenced and hashed.
TRACE_COPY_LIMIT_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------------------
# Determinism helpers
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return frozen.canonical_json(obj)


def digest_of(obj: Any) -> str:
    return frozen.digest_of(obj)


def file_sha256(path: Path) -> str:
    return frozen.file_sha256(path)


def write_json_deterministic(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _hex64(value: Any) -> bool:
    return frozen.is_hex64(value)


# ---------------------------------------------------------------------------
# Output token digest semantics (v1)
# ---------------------------------------------------------------------------

def output_token_digest(tokens: Iterable[int]) -> str:
    """Deterministic digest of a completion token sequence.

    Bytes hashed: ``b"argine-output-tokens-v1\\n"`` followed by each token id in
    order, rendered in decimal ASCII and terminated by ``b"\\n"``::

        argine-output-tokens-v1\\n101\\n2450\\n7\\n

    The digest is therefore independent of prompt text, JSON formatting,
    logging mode and host; it depends only on the ordered token ids. An empty
    sequence has a well-defined digest too (the prefix alone).
    """
    parts = [OUTPUT_DIGEST_PREFIX]
    for t in tokens:
        if not isinstance(t, int) or t < 0:
            raise ValueError(f"token id must be a non-negative int, got {t!r}")
        parts.append(f"{t}\n")
    return hashlib.sha256("".join(parts).encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# Trace archival
# ---------------------------------------------------------------------------

def trace_archive(path: Path) -> Dict[str, Any]:
    """Deterministic archival metadata for a decision trace.

    The trace is referenced and hashed, never duplicated into the repository.
    """
    p = path.resolve()
    size = p.stat().st_size
    try:
        rel = str(p.relative_to(REPO_ROOT))
        inside_repo = True
    except ValueError:
        rel = str(p)
        inside_repo = False
    return {
        "path": rel,
        "inside_repository": inside_repo,
        "sha256": file_sha256(p),
        "bytes": size,
        "trace_format": TRACE_FORMAT_V1,
        "copied_into_repository": False,
        "copy_policy": ("never copied when larger than %d bytes; referenced and hashed only"
                        % TRACE_COPY_LIMIT_BYTES),
    }


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _identity_block(identity: Dict[str, str]) -> Dict[str, str]:
    required = ["model_sha256", "store_sha256", "runtime_sha256", "workload_sha256"]
    for k in required:
        if not _hex64(identity.get(k)):
            raise ValueError(f"identity.{k} must be a 64-hex digest")
    params_digest = identity.get("policy_params_sha256")
    if not _hex64(params_digest):
        raise ValueError("identity.policy_params_sha256 must be a 64-hex digest")
    return {k: identity[k] for k in required + ["policy_params_sha256"]}


def policy_params_digest(policy_params: Dict[str, Any]) -> str:
    return digest_of(policy_params)


def build_trace_count_record(
    *,
    trace_path: Path,
    identity: Dict[str, str],
    workload_id: str,
    seed: Optional[int],
    budget_bytes: int,
    policy_id: str,
    policy_params: Dict[str, Any],
    output_tokens: Optional[Sequence[int]] = None,
    scientific_status: str = "CELL_READY",
    max_lines: Optional[int] = None,
) -> Dict[str, Any]:
    """Derive a TRACE_COUNT record from a retention-decision trace."""
    counters = frozen.extract_decision_trace(Path(trace_path), max_lines=max_lines)
    archive = trace_archive(Path(trace_path))
    if not counters["complete"]:
        status = "INCOMPLETE"
    else:
        status = "COMPLETE"
    slices = 3  # gate / up / down, fixed by the runtime's expert layout
    digest = output_token_digest(output_tokens) if output_tokens is not None else None
    return {
        "telemetry_schema_version": SCHEMA_VERSION,
        "mode": "TRACE_COUNT",
        "scientific_status": scientific_status,
        "contract_digest": CONTRACT_DIGEST,
        "workload_id": workload_id,
        "seed": seed,
        "budget_bytes": int(budget_bytes),
        "policy_id": policy_id,
        "policy_params": dict(sorted(policy_params.items())),
        "output_token_digest": digest,
        "output_token_count": len(output_tokens) if output_tokens is not None else None,
        "identity": _identity_block(identity),
        "telemetry": {
            "mode": "TRACE_COUNT",
            "status": status,
            "trace_sha256": archive["sha256"],
            "archive": archive,
            "counters": {
                "expert_bytes_read": int(counters["miss_bytes"]),
                "expert_read_count": int(counters["miss_expert_events"] * slices),
                "expert_miss_bytes": int(counters["miss_bytes"]),
                "expert_miss_count": int(counters["miss_expert_events"]),
                "expert_hits": int(counters["hit_expert_events"]),
                "expert_evictions": int(counters["evicted_expert_events"]),
                "resident_bytes_peak": int(counters["peak_resident_bytes"]),
                "requested_expert_events": int(counters["requested_expert_events"]),
                "hit_expert_events": int(counters["hit_expert_events"]),
                "miss_expert_events": int(counters["miss_expert_events"]),
                "slices_per_expert": slices,
                "completion_rows": int(counters["completion_rows"]),
                "decision_rows": int(counters["decision_rows"]),
                "max_step": int(counters["max_step"]),
                "budget_violations": int(counters["budget_violations"]),
                "trace_budget_bytes": counters["budget_bytes"],
            },
        },
        "provenance": {
            "generator": "r3-01/telemetry",
            "trace_extractor": "R3-00A extract_decision_trace (frozen)",
            "exact_vs_derived": {
                "expert_bytes_read": "EXACT_RUNTIME",
                "expert_miss_bytes": "EXACT_RUNTIME",
                "expert_read_count": "DERIVED_RUNTIME (3 x expert misses)",
                "resident_bytes_peak": "EXACT_RUNTIME",
                "evictions": "EXACT_RUNTIME",
            },
        },
    }


def build_timing_record(
    *,
    identity: Dict[str, str],
    workload_id: str,
    seed: Optional[int],
    budget_bytes: int,
    policy_id: str,
    policy_params: Dict[str, Any],
    output_tokens: Optional[Sequence[int]],
    ttft_seconds: float,
    decode_duration_seconds: float,
    tokens_generated: int,
    process_cpu_seconds: float,
    events_served: int,
    overhead_process_cpu_seconds: float,
    scientific_status: str = "CELL_READY",
) -> Dict[str, Any]:
    """Build a TIMING record from measured wall-clock and counter metadata."""
    tokens = list(output_tokens) if output_tokens is not None else None
    return {
        "telemetry_schema_version": SCHEMA_VERSION,
        "mode": "TIMING",
        "scientific_status": scientific_status,
        "contract_digest": CONTRACT_DIGEST,
        "workload_id": workload_id,
        "seed": seed,
        "budget_bytes": int(budget_bytes),
        "policy_id": policy_id,
        "policy_params": dict(sorted(policy_params.items())),
        "output_token_digest": output_token_digest(tokens) if tokens is not None else None,
        "output_token_count": len(tokens) if tokens is not None else None,
        "identity": _identity_block(identity),
        "timing": {
            "ttft_seconds": float(ttft_seconds),
            "decode_duration_seconds": float(decode_duration_seconds),
            "tokens_generated": int(tokens_generated),
            "process_cpu_seconds": float(process_cpu_seconds),
            "decode_step_wall_time": float(decode_duration_seconds) / int(tokens_generated)
            if tokens_generated else None,
        },
        "telemetry": {
            "mode": "TIMING",
            "status": "COMPLETE",
            "overhead": {
                "events_served": int(events_served),
                "process_cpu_seconds": float(overhead_process_cpu_seconds),
                "isolated": False,
                "note": ("not isolated: a sham-policy control is required before any "
                         "overhead-difference claim"),
            },
        },
        "provenance": {"generator": "r3-01/telemetry"},
    }


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

EXTRA_RULES = [
    ("X1_CONTRACT_DIGEST", "both records carry the frozen contract digest"),
    ("X2_SCHEMA_VERSION", "both records declare a supported telemetry schema version"),
    ("X3_SEED_PRESENT", "both records declare a seed (a scientific cell cannot have an unknown seed)"),
    ("X4_SCIENTIFIC_STATUS", "both records declare an allowed, identical scientific status"),
    ("X5_OUTPUT_DIGEST", "both records carry a non-null output token digest"),
    ("X6_TRACE_BUDGET", "the trace budget equals the record budget"),
    ("X7_COUNTER_INVARIANTS", "requested == hits + misses, miss bytes == bytes read, reads == 3 x misses"),
]

ALLOWED_STATUS = {"CELL_READY", "OFFLINE_FIXTURE"}


def _failures_from_frozen(reasons: List[str]) -> List[str]:
    out = []
    for r in reasons:
        rule = r.split(":", 1)[0].strip()
        out.append(rule)
    return out


def _trace_counter_failures(record: Dict[str, Any]) -> List[str]:
    """X6/X7 -- invariants that only make sense on a TRACE_COUNT record."""
    if record.get("mode") != "TRACE_COUNT":
        return []
    fails: List[str] = []
    c = (record.get("telemetry") or {}).get("counters") or {}
    tb = c.get("trace_budget_bytes")
    if tb is not None and int(tb) != int(record.get("budget_bytes", -1)):
        fails.append("X6_TRACE_BUDGET")
    try:
        requested = int(c["requested_expert_events"])
        hits = int(c["hit_expert_events"])
        misses = int(c["miss_expert_events"])
        miss_bytes = int(c["expert_miss_bytes"])
        read_bytes = int(c["expert_bytes_read"])
        reads = int(c["expert_read_count"])
        slices = int(c.get("slices_per_expert", 3))
    except (KeyError, TypeError, ValueError):
        return fails + ["X7_COUNTER_INVARIANTS"]
    if requested != hits + misses:
        fails.append("X7_COUNTER_INVARIANTS")
    elif miss_bytes != read_bytes:
        fails.append("X7_COUNTER_INVARIANTS")
    elif reads != misses * slices:
        fails.append("X7_COUNTER_INVARIANTS")
    return fails


def _extra_failures(record_a: Dict[str, Any], record_b: Dict[str, Any]) -> List[str]:
    fails: List[str] = []
    if record_a.get("contract_digest") != CONTRACT_DIGEST or record_b.get("contract_digest") != CONTRACT_DIGEST:
        fails.append("X1_CONTRACT_DIGEST")
    if record_a.get("telemetry_schema_version") != SCHEMA_VERSION or \
            record_b.get("telemetry_schema_version") != SCHEMA_VERSION:
        fails.append("X2_SCHEMA_VERSION")
    if record_a.get("seed") is None or record_b.get("seed") is None:
        fails.append("X3_SEED_PRESENT")
    sa, sb = record_a.get("scientific_status"), record_b.get("scientific_status")
    if sa not in ALLOWED_STATUS or sb not in ALLOWED_STATUS or sa != sb:
        fails.append("X4_SCIENTIFIC_STATUS")
    if not (record_a.get("output_token_digest") and record_b.get("output_token_digest")):
        fails.append("X5_OUTPUT_DIGEST")
    return fails


def _admission(failures: List[str], checks: List[Dict[str, Any]], frozen_ids: List[str]) -> Dict[str, Any]:
    canonical = []
    for f in failures:
        up = f.upper()
        if up not in canonical:
            canonical.append(up)
    return {
        "status": "ADMISSIBLE" if not canonical else "INADMISSIBLE",
        "failures": sorted(canonical),
        "failures_frozen_ids": sorted(set(frozen_ids)),
        "checks": checks,
    }


def admit_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Record-level admission against the frozen contract plus the X rules."""
    mode = record.get("mode")
    out = frozen.validate_run(record, mode) if mode in ("TRACE_COUNT", "TIMING") else \
        {"admitted": False, "checks": [], "reasons": ["V2_mode"]}
    frozen_ids = _failures_from_frozen(out["reasons"])
    fails = list(frozen_ids)
    if record.get("contract_digest") != CONTRACT_DIGEST:
        fails.append("X1_CONTRACT_DIGEST")
    if record.get("telemetry_schema_version") != SCHEMA_VERSION:
        fails.append("X2_SCHEMA_VERSION")
    fails.extend(_trace_counter_failures(record))
    return _admission(fails, out["checks"], frozen_ids)


def admit_cell(trace_record: Dict[str, Any], timing_record: Dict[str, Any]) -> Dict[str, Any]:
    """Pair-level admission: the frozen cell rules plus the X rules."""
    out = frozen.validate_cell(trace_record, timing_record)
    frozen_ids = _failures_from_frozen(out["reasons"])
    fails = list(frozen_ids) + _extra_failures(trace_record, timing_record) + \
        _trace_counter_failures(trace_record)
    checks = out["cell_checks"]
    return _admission(fails, checks, frozen_ids)


def build_cell(trace_record: Dict[str, Any], timing_record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "artifact": "R3-01-CELL",
        "telemetry_schema_version": SCHEMA_VERSION,
        "contract_digest": CONTRACT_DIGEST,
        "admission": admit_cell(trace_record, timing_record),
        "trace_count": trace_record,
        "timing": timing_record,
    }


# ---------------------------------------------------------------------------
# Fixtures (synthetic; no model)
# ---------------------------------------------------------------------------

FIXTURE_IDENTITY = {
    "model_sha256": "a" * 64,
    "store_sha256": "b" * 64,
    "runtime_sha256": "c" * 64,
    "workload_sha256": "d" * 64,
    "policy_params_sha256": "",
}

FIXTURE_PARAMS = {"aging_interval_steps": 64, "decay_factor": 0.99, "n_experts": 0,
                  "predictor_decay": 0, "top_k": 0}


def fixture_identity() -> Dict[str, str]:
    ident = dict(FIXTURE_IDENTITY)
    ident["policy_params_sha256"] = policy_params_digest(FIXTURE_PARAMS)
    return ident


def fixture_tokens() -> List[int]:
    return [101, 2450, 7, 42]


def fixture_trace_count(**overrides) -> Dict[str, Any]:
    kwargs = dict(
        trace_path=PHASE_DIR / "fixtures" / "fixture-trace.jsonl",
        identity=fixture_identity(),
        workload_id="fixture-workload",
        seed=42,
        budget_bytes=4 * 1024 ** 3,
        policy_id="b_selected",
        policy_params=FIXTURE_PARAMS,
        output_tokens=fixture_tokens(),
        scientific_status="CELL_READY",
    )
    kwargs.update(overrides)
    return build_trace_count_record(**kwargs)


def fixture_timing(**overrides) -> Dict[str, Any]:
    kwargs = dict(
        identity=fixture_identity(),
        workload_id="fixture-workload",
        seed=42,
        budget_bytes=4 * 1024 ** 3,
        policy_id="b_selected",
        policy_params=FIXTURE_PARAMS,
        output_tokens=fixture_tokens(),
        ttft_seconds=10.0,
        decode_duration_seconds=120.0,
        tokens_generated=512,
        process_cpu_seconds=900.0,
        events_served=1234,
        overhead_process_cpu_seconds=0.4,
        scientific_status="CELL_READY",
    )
    kwargs.update(overrides)
    return build_timing_record(**kwargs)
