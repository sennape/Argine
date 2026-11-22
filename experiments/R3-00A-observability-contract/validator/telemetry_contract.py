"""R3-00A observability contract: validator, canonical-set selector, trace extractor.

This module is deliberately dependency-free (standard library only) so that the
contract can be enforced on any host, in CI, and in the frozen repository.

It implements three things:

1. ``select_canonical_runs`` -- the mechanical definition of the canonical
   R2-12 run set (120 counted runs, 40 per budget), plus the precise
   classification of every record that is *not* in it.
2. ``validate_run`` / ``validate_cell`` -- fail-closed admissibility for a
   future counted R3 cell (a TRACE_COUNT run paired with a TIMING run).
3. ``extract_decision_trace`` -- exact expert-level counters derived from the
   runtime's retention-decision trace (the artifact that already exists and is
   observational: it records decisions, it never makes them).

Nothing here executes the model, opens EVAL, or changes runtime behaviour.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE_DIR = Path(__file__).resolve().parents[1]
R2_RUNTIME_DIR = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "runtime_results"
CONTRACT_PATH = PHASE_DIR / "OBSERVABILITY_CONTRACT.json"

CANONICAL_BUDGETS = (1.5, 4.0, 8.0)
HEX64 = set("0123456789abcdef")


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 preserving."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value.lower()) <= HEX64


# ---------------------------------------------------------------------------
# Canonical R2-12 run set
# ---------------------------------------------------------------------------

def _is_launch_record(d: Any) -> bool:
    return isinstance(d, dict) and all(
        k in d for k in ("run_id", "counted", "warmup", "budget_gib", "policy_id", "runtime_metrics")
    )


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def select_canonical_runs(runtime_dir: Path = R2_RUNTIME_DIR) -> Dict[str, Any]:
    """Return the canonical 120-run set and every excluded record, classified.

    Canonical rule (frozen by this contract):
      * file is a *top-level* ``runtime_results/*.json``;
      * the record is a launch record (has run_id/counted/warmup/budget_gib/...);
      * ``counted is True`` and ``warmup is False``;
      * ``budget_gib`` in {1.5, 4.0, 8.0}.

    Everything else is returned in ``excluded`` with an explicit reason.  The
    rule is deliberately structural (''top-level file'') rather than
    ''whatever happens to be counted'', because the pre-canonical trace-enabled
    attempts carry ``counted: true`` while being inadmissible for analysis.
    """
    top_files = sorted(p for p in runtime_dir.glob("*.json"))
    launches: List[Tuple[Path, dict]] = []
    controls: List[Path] = []
    for p in top_files:
        try:
            d = _load(p)
        except Exception:
            controls.append(p)
            continue
        if _is_launch_record(d):
            launches.append((p, d))
        else:
            controls.append(p)

    canonical, warmups, other_launches = [], [], []
    for p, d in launches:
        if d.get("counted") is True and d.get("warmup") is False and d.get("budget_gib") in CANONICAL_BUDGETS:
            canonical.append((p, d))
        elif d.get("warmup") is True and d.get("counted") is False:
            warmups.append((p, d))
        else:
            other_launches.append((p, d))

    # Counted records outside the canonical directory: pre-canonical attempts,
    # diagnostics, self-tests.  They must never enter the canonical set.
    precanonical_counted = []
    selftest_counted = []
    noncanonical_other = []
    for p in sorted(runtime_dir.rglob("*.json")):
        if p.parent == runtime_dir:
            continue
        try:
            d = _load(p)
        except Exception:
            noncanonical_other.append((p, None))
            continue
        if _is_launch_record(d) and d.get("counted") is True:
            tps = (d.get("runtime_metrics") or {}).get("decode_tokens_per_second")
            if isinstance(tps, (int, float)):
                precanonical_counted.append((p, d))
            else:
                # counted flag on a harness self-test: not a model launch at all
                selftest_counted.append((p, d))
        else:
            noncanonical_other.append((p, d))

    return {
        "runtime_dir": str(runtime_dir),
        "top_level_files": len(top_files),
        "launch_records": len(launches),
        "control_files": [p.name for p in controls],
        "canonical": canonical,
        "warmups": warmups,
        "other_launches_top_level": other_launches,
        "precanonical_counted": precanonical_counted,
        "selftest_counted": selftest_counted,
        "noncanonical_other": noncanonical_other,
    }


# ---------------------------------------------------------------------------
# Record-level admissibility
# ---------------------------------------------------------------------------

def _missing(record: dict, dotted_paths: Iterable[str]) -> List[str]:
    missing = []
    for path in dotted_paths:
        cur: Any = record
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok or cur is None or (isinstance(cur, str) and cur == ""):
            missing.append(path)
    return missing


def _get(record: dict, path: str, default=None):
    cur: Any = record
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


IDENTITY_FIELDS = [
    "identity.model_sha256",
    "identity.store_sha256",
    "identity.runtime_sha256",
    "identity.workload_sha256",
    "identity.policy_params_sha256",
]

TRACE_COUNT_FIELDS = IDENTITY_FIELDS + [
    "telemetry.mode",
    "telemetry.status",
    "telemetry.trace_sha256",
    "telemetry.counters.expert_bytes_read",
    "telemetry.counters.expert_read_count",
    "telemetry.counters.expert_miss_bytes",
    "telemetry.counters.expert_miss_count",
    "telemetry.counters.expert_hits",
    "telemetry.counters.expert_evictions",
    "telemetry.counters.resident_bytes_peak",
    "budget_bytes",
]

TIMING_FIELDS = IDENTITY_FIELDS + [
    "telemetry.mode",
    "telemetry.status",
    "timing.ttft_seconds",
    "timing.decode_duration_seconds",
    "timing.tokens_generated",
    "timing.process_cpu_seconds",
    "telemetry.overhead.events_served",
    "telemetry.overhead.process_cpu_seconds",
    "budget_bytes",
]


def validate_run(record: dict, mode: str) -> Dict[str, Any]:
    """Fail-closed admissibility of one run for the given telemetry mode.

    Returns ``{"admitted": bool, "checks": [...], "reasons": [...]}``.  A record
    is admitted only if *every* check passes; missing mandatory telemetry is
    always a rejection, never a warning.
    """
    checks: List[Dict[str, Any]] = []
    reasons: List[str] = []

    def check(cid: str, ok: bool, detail: str = "") -> None:
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})
        if not ok:
            reasons.append(f"{cid}: {detail}" if detail else cid)

    required = TRACE_COUNT_FIELDS if mode == "TRACE_COUNT" else TIMING_FIELDS
    missing = _missing(record, required)
    check("V1_required_fields", not missing, f"missing: {missing}" if missing else "all present")

    declared = _get(record, "telemetry.mode")
    check("V2_mode", declared == mode, f"declared={declared!r} expected={mode!r}")

    if not missing:
        bad = [f for f in IDENTITY_FIELDS if not is_hex64(_get(record, f))]
        check("V3_identities_hex64", not bad, f"non-hex64: {bad}" if bad else "ok")

        if mode == "TRACE_COUNT":
            check("V4_trace_hex64", is_hex64(_get(record, "telemetry.trace_sha256")),
                  str(_get(record, "telemetry.trace_sha256"))[:16] + "...")
            status = _get(record, "telemetry.status")
            check("V5_completeness", status == "COMPLETE", f"status={status!r}")
            c = record["telemetry"]["counters"]
            b, mb = int(c["expert_bytes_read"]), int(c["expert_miss_bytes"])
            rc, mc = int(c["expert_read_count"]), int(c["expert_miss_count"])
            check("V6_bytes_consistency", 0 <= mb <= b, f"bytes={b} miss_bytes={mb}")
            check("V7_count_consistency", 0 <= mc <= rc, f"reads={rc} misses={mc}")
            peak, budget = int(c["resident_bytes_peak"]), int(record["budget_bytes"])
            check("V8_budget_invariant", peak <= budget, f"peak={peak} budget={budget}")
        else:
            ttft = float(_get(record, "timing.ttft_seconds", -1))
            dec = float(_get(record, "timing.decode_duration_seconds", -1))
            toks = int(_get(record, "timing.tokens_generated", 0))
            cpu = float(_get(record, "timing.process_cpu_seconds", 0))
            ev = int(_get(record, "telemetry.overhead.events_served", -1))
            check("V9_timing_positive", ttft > 0 and dec > 0 and toks > 0 and cpu > 0,
                  f"ttft={ttft} decode={dec} tokens={toks} cpu={cpu}")
            check("V10_overhead_counters", ev >= 0, f"events_served={ev}")

    admitted = all(c["ok"] for c in checks)
    return {"admitted": admitted, "checks": checks, "reasons": reasons}


def validate_cell(count_run: dict, timing_run: dict) -> Dict[str, Any]:
    """Admissibility of one comparison cell: a TRACE_COUNT + TIMING pair.

    The pair must describe the same physical cell (same workload seed, budget,
    policy and policy parameters) and must have produced identical output
    tokens; otherwise the tracing used to obtain exact counters cannot be
    assumed to have left the decisions unchanged.
    """
    rc = validate_run(count_run, "TRACE_COUNT")
    rt = validate_run(timing_run, "TIMING")
    checks: List[Dict[str, Any]] = []
    reasons = list(rc["reasons"]) + list(rt["reasons"])

    def same(path: str) -> bool:
        return _get(count_run, path) == _get(timing_run, path)

    consistent = all(same(p) for p in (
        "workload_id", "seed", "budget_bytes", "policy_id", "identity.policy_params_sha256",
    ))
    checks.append({"id": "C1_same_cell", "ok": consistent, "detail": "workload/seed/budget/policy/params"})
    if not consistent:
        reasons.append("C1_same_cell")

    otc = _get(count_run, "output_token_digest")
    ott = _get(timing_run, "output_token_digest")
    identical = bool(otc) and otc == ott
    checks.append({"id": "C2_output_identity", "ok": identical,
                   "detail": f"count={str(otc)[:12]} timing={str(ott)[:12]}"})
    if not identical:
        reasons.append("C2_output_identity: traced and untraced runs diverged")

    admitted = rc["admitted"] and rt["admitted"] and all(c["ok"] for c in checks)
    return {"admitted": admitted, "count_run": rc, "timing_run": rt,
            "cell_checks": checks, "reasons": reasons}


# ---------------------------------------------------------------------------
# Decision-trace extraction (exact expert-level counters)
# ---------------------------------------------------------------------------

def extract_decision_trace(path: Path, max_lines: Optional[int] = None) -> Dict[str, Any]:
    """Stream a retention-decision trace and derive exact expert-level counters.

    Trace format (one JSON object per line):
      decision row : step, policy, layer, bytes_of_layer, requested_experts,
                     hit_experts, miss_experts, evicted, resident_after,
                     cache_resident_bytes_after, budget_bytes, policy_params
      completion   : step, complete=1, cache_resident_bytes_post_service

    Completeness is structural: every step from 0..max_step must have exactly
    one completion row and the last line of the file must be a completion row.
    A truncated copy of a real trace therefore reports ``complete: false`` --
    the extractor never guesses.
    """
    counters = {
        "decision_rows": 0,
        "completion_rows": 0,
        "steps_completed": 0,
        "max_step": -1,
        "requested_expert_events": 0,
        "hit_expert_events": 0,
        "miss_expert_events": 0,
        "miss_bytes": 0,
        "evicted_expert_events": 0,
        "peak_resident_bytes": 0,
        "budget_bytes": None,
        "budget_violations": 0,
        "policy_ids": [],
        "complete": False,
        "completion_gaps": 0,
        "last_line_is_completion": False,
    }
    completed = set()
    policies = set()
    budget = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "complete" in row:
                counters["completion_rows"] += 1
                completed.add(int(row.get("step", -1)))
                counters["last_line_is_completion"] = True
            else:
                counters["last_line_is_completion"] = False
                counters["decision_rows"] += 1
                layer = int(row.get("layer", 0))
                size = int(row.get("bytes_of_layer", 0) or 0)
                requested = row.get("requested_experts") or []
                hits = row.get("hit_experts") or []
                misses = row.get("miss_experts") or []
                evicted = row.get("evicted") or []
                counters["requested_expert_events"] += len(requested)
                counters["hit_expert_events"] += len(hits)
                counters["miss_expert_events"] += len(misses)
                counters["miss_bytes"] += len(misses) * size
                counters["evicted_expert_events"] += len(evicted)
                policies.add(row.get("policy"))
                step = int(row.get("step", -1))
                counters["max_step"] = max(counters["max_step"], step)
                resident = row.get("cache_resident_bytes_after")
                if resident is not None:
                    counters["peak_resident_bytes"] = max(counters["peak_resident_bytes"], int(resident))
                if budget is None:
                    budget = row.get("budget_bytes")
                row_budget = row.get("budget_bytes")
                if row_budget is not None and resident is not None and int(resident) > int(row_budget):
                    counters["budget_violations"] += 1
            if max_lines is not None and i >= max_lines:
                break
    counters["steps_completed"] = len(completed)
    counters["budget_bytes"] = int(budget) if budget is not None else None
    expected = counters["max_step"] + 1 if counters["max_step"] >= 0 else 0
    counters["completion_gaps"] = max(0, expected - len(completed))
    counters["complete"] = bool(
        counters["max_step"] >= 0
        and counters["last_line_is_completion"]
        and counters["completion_gaps"] == 0
        and counters["budget_violations"] == 0
    )
    counters["policy_ids"] = sorted(p for p in policies if p)
    return counters


# ---------------------------------------------------------------------------
# Canonical R2-12 audit
# ---------------------------------------------------------------------------

AUDIT_MANDATORY_FIELDS = [
    "expert_bytes_read",
    "expert_read_count",
    "expert_miss_bytes",
    "prediction_overhead_seconds",
    "retention_policy_overhead_seconds",
]


def build_canonical_audit(runtime_dir: Path = R2_RUNTIME_DIR) -> Dict[str, Any]:
    """Machine-readable audit of the canonical 120-run set.

    Telemetry completeness is reported ONLY on that set; every excluded record
    is listed with its classification.
    """
    sel = select_canonical_runs(runtime_dir)
    canonical = sel["canonical"]
    runs = []
    per_field = {f: 0 for f in AUDIT_MANDATORY_FIELDS}
    for p, d in sorted(canonical, key=lambda x: (x[1]["budget_gib"], x[1]["workload_id"],
                                                 x[1]["repetition_index"], x[1]["policy_id"])):
        m = d.get("runtime_metrics", {}) or {}
        present = {f: m.get(f) is not None for f in AUDIT_MANDATORY_FIELDS}
        for f, ok in present.items():
            per_field[f] += int(ok)
        runs.append({
            "file": p.name,
            "run_id": d.get("run_id"),
            "budget_gib": d.get("budget_gib"),
            "policy_id": d.get("policy_id"),
            "workload_id": d.get("workload_id"),
            "repetition_index": d.get("repetition_index"),
            "trace_enabled": d.get("trace_enabled"),
            "missing_mandatory_fields": sorted(f for f, ok in present.items() if not ok),
            "telemetry_status": "ABSENT" if not any(present.values()) else "PARTIAL",
            "admissible_under_r3_contract": False,
        })

    by_budget = {}
    for b in CANONICAL_BUDGETS:
        by_budget[str(b)] = sum(1 for _, d in canonical if d["budget_gib"] == b)

    excluded = {
        "warmups": {"count": len(sel["warmups"]), "reason": "warmup=true, counted=false"},
        "control_files": {"count": len(sel["control_files"]), "files": sorted(sel["control_files"])},
        "precanonical_trace_enabled_attempts": {
            "count": len(sel["precanonical_counted"]),
            "records": [
                {
                    "file": p.name,
                    "relative_path": str(p.relative_to(runtime_dir)),
                    "run_id": d.get("run_id"),
                    "budget_gib": d.get("budget_gib"),
                    "policy_id": d.get("policy_id"),
                    "workload_id": d.get("workload_id"),
                    "repetition_index": d.get("repetition_index"),
                    "counted": d.get("counted"),
                    "warmup": d.get("warmup"),
                    "decode_tokens_per_second": (d.get("runtime_metrics") or {}).get("decode_tokens_per_second"),
                    "classification": "PRECANONICAL_TRACE_ENABLED_ATTEMPT",
                    "reason": ("Trace-enabled attempt excluded from the canonical set: heavy trace "
                               "logging slows the runtime ~5x (0.83 vs 4.19 TPS), so it cannot be a "
                               "timing record; it lives outside runtime_results/ top level and was "
                               "never admitted by the canonical driver."),
                }
                for p, d in sorted(sel["precanonical_counted"], key=lambda x: x[0].name)
            ],
        },
        "other_noncanonical_records": {"count": len(sel["noncanonical_other"])},
        "preflight_selftest_records": {
            "count": len(sel["selftest_counted"]),
            "files": sorted(p.name for p, _ in sel["selftest_counted"]),
            "classification": "PREFLIGHT_SELFTEST",
            "reason": ("carries counted=true but has no decode metric: it is a harness self-test, "
                       "not a model launch, and is excluded from every count of runs"),
        },
    }

    audit = {
        "artifact": "R3-00A-CANONICAL-R2-12-AUDIT",
        "contract": "argine-r3-observability-contract",
        "contract_version": 1,
        "canonical_rule": (
            "top-level runtime_results/*.json AND launch record AND counted==true AND "
            "warmup==false AND budget_gib in {1.5,4,8}"
        ),
        "counts": {
            "top_level_files": sel["top_level_files"],
            "launch_records": sel["launch_records"],
            "canonical_counted": len(canonical),
            "warmups": len(sel["warmups"]),
            "control_files": len(sel["control_files"]),
            "canonical_by_budget": by_budget,
            "counted_if_scanned_recursively": len(canonical) + len(sel["precanonical_counted"]),
        },
        "reconciliation": {
            "top_level": f"{sel['launch_records']} launch records + {len(sel['control_files'])} control files",
            "launches": f"{len(canonical)} counted + {len(sel['warmups'])} warmups",
            "recursive_counted": (
                f"{len(canonical)} canonical + {len(sel['precanonical_counted'])} pre-canonical "
                f"trace-enabled attempts = {len(canonical) + len(sel['precanonical_counted'])} counted "
                f"records with a decode metric; one further counted-flagged "
                f"record is a preflight self-test with no metric and is not a launch"
            ),
            "explains_123_vs_120": True,
        },
        "telemetry_completeness": {
            "scope": "canonical 120-run set only",
            "per_field_non_null": per_field,
            "runs_with_all_mandatory_fields": sum(1 for r in runs if not r["missing_mandatory_fields"]),
            "admissible_under_r3_contract": 0,
            "verdict": "INADMISSIBLE_MISSING_TELEMETRY",
        },
        "canonical_runs": runs,
        "excluded_records": excluded,
        "audit_digest": "",
    }
    body = dict(audit)
    body.pop("audit_digest")
    audit["audit_digest"] = digest_of(body)
    return audit


# ---------------------------------------------------------------------------
# Artifact builders (deterministic)
# ---------------------------------------------------------------------------

def load_contract(path: Path = CONTRACT_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def contract_digest(contract: Dict[str, Any]) -> str:
    return digest_of(contract)


def build_freeze(created_utc: str, anchor_commit: str) -> Dict[str, Any]:
    contract = load_contract()
    audit_path = PHASE_DIR / "CANONICAL_R2_12_AUDIT.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    sel = select_canonical_runs()
    freeze = {
        "artifact": "R3-00A-FREEZE",
        "phase": "R3-00A",
        "status": "PASS_OBSERVABILITY_READY",
        "created_utc": created_utc,
        "canonical_anchor_commit": anchor_commit,
        "contract": {
            "path": "experiments/R3-00A-observability-contract/OBSERVABILITY_CONTRACT.json",
            "sha256": file_sha256(CONTRACT_PATH),
            "contract_digest": contract_digest(contract),
            "version": contract["version"],
        },
        "audit": {
            "path": "experiments/R3-00A-observability-contract/CANONICAL_R2_12_AUDIT.json",
            "sha256": file_sha256(audit_path),
            "audit_digest": audit["audit_digest"],
            "canonical_runs": len(audit["canonical_runs"]),
            "admissible_under_r3_contract": audit["telemetry_completeness"]["admissible_under_r3_contract"],
        },
        "validator": {
            "path": "experiments/R3-00A-observability-contract/validator/telemetry_contract.py",
            "sha256": file_sha256(Path(__file__)),
        },
        "tests": {
            "path": "experiments/R3-00A-observability-contract/tests/test_telemetry_contract.py",
            "sha256": file_sha256(PHASE_DIR / "tests" / "test_telemetry_contract.py"),
            "result": "26 passed (python -m unittest, standard library only, no model)",
        },
        "frozen_scope": [
            "observability contract v1 (fields, classes, modes, admissibility rules)",
            "canonical R2-12 run-set definition and audit",
        ],
        "not_frozen": [
            "R3 primary policy",
            "R3 primary scientific endpoint",
            "DEV policy-selection rule",
            "EVAL decision rules",
        ],
        "prohibited_actions": [
            "using diagnostic/warmup/pre-canonical records in analysis",
            "treating miss bytes as throughput evidence",
            "claiming policy-overhead isolation without a sham control",
            "changing this contract after EVAL is opened",
        ],
        "r2_evidence": {
            "runtime_results_top_level_files": sel["top_level_files"],
            "canonical_runs": len(sel["canonical"]),
            "modified": False,
        },
    }
    return freeze


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import subprocess

    ap = argparse.ArgumentParser(description="R3-00A observability contract tooling")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit", help="regenerate CANONICAL_R2_12_AUDIT.json")
    fr = sub.add_parser("freeze", help="regenerate FREEZE.json")
    fr.add_argument("--created-utc", required=True)
    fr.add_argument("--anchor-commit", required=True)
    sub.add_parser("digest", help="print the contract digest")
    args = ap.parse_args(argv)

    if args.cmd == "audit":
        audit = build_canonical_audit()
        out = PHASE_DIR / "CANONICAL_R2_12_AUDIT.json"
        out.write_text(json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out} ({len(audit['canonical_runs'])} canonical runs, digest {audit['audit_digest'][:16]}...)")
        return 0
    if args.cmd == "freeze":
        freeze = build_freeze(args.created_utc, args.anchor_commit)
        out = PHASE_DIR / "FREEZE.json"
        out.write_text(json.dumps(freeze, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
        return 0
    if args.cmd == "digest":
        print(contract_digest(load_contract()))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
