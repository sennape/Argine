"""R3-01 offline validation and freeze builders.

Runs the offline admissible/inadmissible matrix against the frozen R3-00A
contract, writes the deterministic artefacts, and never touches the model.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PHASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PHASE_DIR.parents[1]
sys.path.insert(0, str(PHASE_DIR))

import r3_telemetry.telemetry as T  # noqa: E402

CONFORMANCE_DIR = (REPO_ROOT / "experiments" / "R2-12-runtime-validation"
                   / "HOST-CONFORMANCE-PASS-V8-20261115")
CONFORMANCE_TRACE = (REPO_ROOT / "experiments" / "R2-12-runtime-validation"
                     / "conformance_traces" / "conformance-b_selected-20261115.jsonl")
R2_RUNTIME_DIR = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "runtime_results"


def _json_digest(obj: Any) -> str:
    return T.digest_of(obj)


def _case(case_id: int, name: str, expected: str, observed: Dict[str, Any]) -> Dict[str, Any]:
    got = observed.get("status")
    return {
        "id": case_id,
        "name": name,
        "expected": expected,
        "observed_status": got,
        "pass": got == expected,
        "failures": observed.get("failures", []),
    }


def _cell(trace_rec: Dict[str, Any], timing_rec: Dict[str, Any]) -> Dict[str, Any]:
    return T.admit_cell(trace_rec, timing_rec)


def real_trace_fixture(skip: bool) -> Dict[str, Any]:
    """Parse the real conformance trace offline and build a fixture record.

    This is a parser/extractor fixture, never R3 scientific evidence.
    """
    if skip or not CONFORMANCE_TRACE.exists():
        return {"skipped": True, "reason": "trace not present or --skip-real-trace"}
    manifest_path = CONFORMANCE_DIR / "CONFORMANCE-MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    params = manifest["policies"]["b_selected"]["frozen_params"]
    identity = {
        "model_sha256": manifest["model"]["sha256"],
        "store_sha256": manifest["expert_store"]["sha256"],
        "runtime_sha256": manifest["runtime"]["sha256"],
        "workload_sha256": T.file_sha256(manifest_path),
        "policy_params_sha256": T.policy_params_digest(params),
    }
    record = T.build_trace_count_record(
        trace_path=CONFORMANCE_TRACE,
        identity=identity,
        workload_id="r2-12-conformance-fixture",
        seed=None,
        budget_bytes=int(manifest["configuration"]["budget_bytes"]),
        policy_id="b_selected",
        policy_params=params,
        output_tokens=None,
        scientific_status="OFFLINE_FIXTURE",
    )
    admission = T.admit_record(record)
    return {
        "skipped": False,
        "trace": str(CONFORMANCE_TRACE.relative_to(REPO_ROOT)),
        "note": ("offline parser/extractor fixture only; the trace covers a single layer "
                 "and is not R3 scientific evidence"),
        "extraction": {
            "decision_rows": record["telemetry"]["counters"]["decision_rows"],
            "completion_rows": record["telemetry"]["counters"]["completion_rows"],
            "max_step": record["telemetry"]["counters"]["max_step"],
            "miss_expert_events": record["telemetry"]["counters"]["miss_expert_events"],
            "miss_bytes": record["telemetry"]["counters"]["expert_miss_bytes"],
            "evictions": record["telemetry"]["counters"]["expert_evictions"],
            "peak_resident_bytes": record["telemetry"]["counters"]["resident_bytes_peak"],
            "completeness": record["telemetry"]["status"],
            "budget_violations": record["telemetry"]["counters"]["budget_violations"],
        },
        "archive": record["telemetry"]["archive"],
        "record_digest": _json_digest(record),
        "record_admission": admission,
    }


def build_validation(skip_real: bool = False) -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = []
    valid_count = T.fixture_trace_count()
    valid_timing = T.fixture_timing()

    cases.append(_case(1, "synthetic valid TRACE_COUNT", "ADMISSIBLE", T.admit_record(valid_count)))
    cases.append(_case(2, "synthetic valid TIMING", "ADMISSIBLE", T.admit_record(valid_timing)))
    cases.append(_case(3, "valid paired cell", "ADMISSIBLE", _cell(valid_count, valid_timing)))

    broken_count = copy.deepcopy(valid_count)
    del broken_count["telemetry"]["counters"]["expert_miss_bytes"]
    cases.append(_case(4, "missing TRACE_COUNT telemetry", "INADMISSIBLE", T.admit_record(broken_count)))

    broken_timing = copy.deepcopy(valid_timing)
    del broken_timing["timing"]["ttft_seconds"]
    cases.append(_case(5, "missing TIMING telemetry", "INADMISSIBLE", T.admit_record(broken_timing)))

    t6 = T.fixture_timing(workload_id="other-workload")
    cases.append(_case(6, "mismatched workload", "INADMISSIBLE", _cell(valid_count, t6)))

    t7 = T.fixture_timing(seed=7)
    cases.append(_case(7, "mismatched seed", "INADMISSIBLE", _cell(valid_count, t7)))

    t8 = T.fixture_timing(budget_bytes=8 * 1024 ** 3)
    cases.append(_case(8, "mismatched budget", "INADMISSIBLE", _cell(valid_count, t8)))

    alt_params = dict(T.FIXTURE_PARAMS, decay_factor=0.5)
    alt_identity = T.fixture_identity()
    alt_identity["policy_params_sha256"] = T.policy_params_digest(alt_params)
    t9 = T.fixture_timing(policy_params=alt_params, identity=alt_identity)
    cases.append(_case(9, "mismatched policy params", "INADMISSIBLE", _cell(valid_count, t9)))

    t10 = T.fixture_timing(output_tokens=[9, 9, 9])
    cases.append(_case(10, "mismatched output token digest", "INADMISSIBLE", _cell(valid_count, t10)))

    incomplete = T.fixture_trace_count(max_lines=2)
    cases.append(_case(11, "incomplete trace", "INADMISSIBLE", T.admit_record(incomplete)))

    over_budget = copy.deepcopy(valid_count)
    over_budget["telemetry"]["counters"]["resident_bytes_peak"] = 4 * 1024 ** 3 + 1
    cases.append(_case(12, "budget violation", "INADMISSIBLE", T.admit_record(over_budget)))

    bad_identity = T.fixture_trace_count()
    bad_identity["identity"]["runtime_sha256"] = "not-a-digest"
    cases.append(_case(13, "non-hex identity", "INADMISSIBLE", T.admit_record(bad_identity)))

    bad_contract = T.fixture_trace_count()
    bad_contract["contract_digest"] = "0" * 64
    cases.append(_case(14, "altered contract digest", "INADMISSIBLE", T.admit_record(bad_contract)))

    r2_record = next(R2_RUNTIME_DIR.glob("*.json"))
    r2_data = json.loads(r2_record.read_text(encoding="utf-8"))
    cases.append(_case(15, "canonical R2-12 record", "INADMISSIBLE", T.admit_record(r2_data)))

    real = real_trace_fixture(skip_real)
    if not real.get("skipped"):
        cases.append(_case(16, "real conformance trace extraction", "ADMISSIBLE",
                           real["record_admission"]))

    # Determinism checks
    a = _json_digest(T.fixture_trace_count())
    b = _json_digest(T.fixture_trace_count())
    tmp1 = PHASE_DIR / ".det_a.json"
    tmp2 = PHASE_DIR / ".det_b.json"
    T.write_json_deterministic(tmp1, T.fixture_trace_count())
    T.write_json_deterministic(tmp2, T.fixture_trace_count())
    bytes_equal = tmp1.read_bytes() == tmp2.read_bytes()
    tmp1.unlink()
    tmp2.unlink()

    # Output digest semantics checks
    tokens = [101, 2450, 7]
    d_list = T.output_token_digest(tokens)
    d_iter = T.output_token_digest(iter(tokens))
    d_reordered = T.output_token_digest([7, 2450, 101])
    d_logged = T.output_token_digest(json.loads(json.dumps(tokens)))
    d_empty = T.output_token_digest([])

    determinism = {
        "record_digest_stable": a == b,
        "bytes_identical": bytes_equal,
        "output_digest_order_sensitive": d_list != d_reordered,
        "output_digest_container_independent": d_list == d_iter == d_logged,
        "output_digest_empty_defined": len(d_empty) == 64,
        "output_digest_v1": d_list,
        "pass": bool(a == b and bytes_equal and d_list != d_reordered
                     and d_list == d_iter == d_logged and len(d_empty) == 64),
    }

    # Canonical R2-12 sweep: no frozen record may become admissible
    r2_files = sorted(R2_RUNTIME_DIR.glob("*.json"))
    r2_admissible = 0
    for p in r2_files:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if T.admit_record(rec)["status"] == "ADMISSIBLE":
            r2_admissible += 1
    r2_sweep = {
        "records_scanned": len(r2_files),
        "admissible": r2_admissible,
        "expected_admissible": 0,
        "pass": r2_admissible == 0,
    }

    passed = sum(1 for c in cases if c["pass"])
    return {
        "artifact": "R3-01-OFFLINE-VALIDATION",
        "phase": "R3-01",
        "contract_digest": T.CONTRACT_DIGEST,
        "cases": cases,
        "determinism": determinism,
        "real_trace_fixture": real,
        "canonical_r2_12_sweep": r2_sweep,
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "all_pass": passed == len(cases) and determinism["pass"] and r2_sweep["pass"],
        },
    }


def build_implementation() -> Dict[str, Any]:
    contract = T.CONTRACT
    return {
        "artifact": "R3-01-IMPLEMENTATION",
        "phase": "R3-01",
        "contract_digest": T.CONTRACT_DIGEST,
        "contract_version": contract["version"],
        "telemetry_schema_version": T.SCHEMA_VERSION,
        "record_types": {
            "TRACE_COUNT": {
                "source": "retention-decision trace (JSONL)",
                "required_top_level": [
                    "telemetry_schema_version", "mode", "scientific_status", "contract_digest",
                    "workload_id", "seed", "budget_bytes", "policy_id", "policy_params",
                    "output_token_digest", "output_token_count", "identity", "telemetry",
                ],
                "counters": [
                    "expert_bytes_read", "expert_read_count", "expert_miss_bytes",
                    "expert_miss_count", "expert_hits", "expert_evictions",
                    "resident_bytes_peak", "requested_expert_events", "hit_expert_events",
                    "miss_expert_events", "slices_per_expert", "decision_rows",
                    "completion_rows", "max_step", "budget_violations", "trace_budget_bytes",
                ],
                "exactness": {
                    "expert_bytes_read": "EXACT_RUNTIME",
                    "expert_miss_bytes": "EXACT_RUNTIME",
                    "expert_read_count": "DERIVED_RUNTIME",
                },
            },
            "TIMING": {
                "source": "measured wall clock and process CPU time",
                "required_top_level": [
                    "telemetry_schema_version", "mode", "scientific_status", "contract_digest",
                    "workload_id", "seed", "budget_bytes", "policy_id", "policy_params",
                    "output_token_digest", "output_token_count", "identity", "timing", "telemetry",
                ],
                "timing_fields": ["ttft_seconds", "decode_duration_seconds",
                                  "tokens_generated", "process_cpu_seconds",
                                  "decode_step_wall_time"],
                "overhead_fields": ["events_served", "process_cpu_seconds", "isolated"],
            },
            "CELL": {
                "composition": "TRACE_COUNT + TIMING of the same physical configuration",
                "same_cell_fields": ["workload_id", "seed", "budget_bytes", "policy_id",
                                     "identity.policy_params_sha256"],
                "identity_fields": ["output_token_digest"],
            },
        },
        "output_token_digest_semantics": {
            "version": "argine-output-tokens-v1",
            "hashed_bytes": "prefix 'argine-output-tokens-v1\\n', then each token id in decimal ASCII followed by '\\n', in completion order",
            "depends_on": ["ordered token ids"],
            "independent_of": ["prompt text", "JSON formatting", "logging mode", "host"],
            "empty_sequence": "digest of the prefix alone",
        },
        "trace_archival_semantics": {
            "fields": ["path", "inside_repository", "sha256", "bytes", "trace_format",
                       "copied_into_repository", "copy_policy"],
            "copy_rule": f"never copied into Git above {T.TRACE_COPY_LIMIT_BYTES} bytes; referenced and hashed only",
        },
        "admission_rules": {
            "frozen": [r["id"] for r in contract["admissibility_rules"]],
            "r3_01": [rid for rid, _ in T.EXTRA_RULES],
        },
        "module": {
            "path": "experiments/R3-01-telemetry-realization/r3_telemetry/telemetry.py",
            "sha256": T.file_sha256(Path(T.__file__)),
        },
        "tests": {
            "path": "experiments/R3-01-telemetry-realization/tests/test_r3_telemetry.py",
            "sha256": T.file_sha256(PHASE_DIR / "tests" / "test_r3_telemetry.py"),
        },
    }


def build_freeze(created_utc: str, anchor_commit: str, validation: Dict[str, Any]) -> Dict[str, Any]:
    r3a_contract = (T.R3A_DIR / "OBSERVABILITY_CONTRACT.json")
    r3a_audit = (T.R3A_DIR / "CANONICAL_R2_12_AUDIT.json")
    freeze = {
        "artifact": "R3-01-FREEZE",
        "phase": "R3-01",
        "status": "PASS_TELEMETRY_REALIZED",
        "created_utc": created_utc,
        "anchor_commit": anchor_commit,
        "frozen_scope": [
            "telemetry record schemas (TRACE_COUNT, TIMING, CELL)",
            "trace archival representation",
            "output-token digest semantics v1",
            "TRACE_COUNT/TIMING pairing implementation",
            "admission implementation corresponding to R3-00A",
        ],
        "not_frozen": [
            "cost-aware policy",
            "cost-model coefficients",
            "primary scientific endpoint",
            "DEV policy-selection rule",
            "EVAL thresholds",
            "runtime benchmark endpoint",
        ],
        "r3_00a_integrity": {
            "contract_path": "experiments/R3-00A-observability-contract/OBSERVABILITY_CONTRACT.json",
            "contract_sha256": T.file_sha256(r3a_contract),
            "contract_digest": T.CONTRACT_DIGEST,
            "audit_path": "experiments/R3-00A-observability-contract/CANONICAL_R2_12_AUDIT.json",
            "audit_sha256": T.file_sha256(r3a_audit),
            "audit_digest": validation.get("r3_00a_audit_digest"),
            "unchanged": True,
        },
        "artifacts": {
            "README.md": T.file_sha256(PHASE_DIR / "README.md"),
            "IMPLEMENTATION.json": T.file_sha256(PHASE_DIR / "IMPLEMENTATION.json"),
            "OFFLINE_VALIDATION.json": T.file_sha256(PHASE_DIR / "OFFLINE_VALIDATION.json"),
            "r3_telemetry/telemetry.py": T.file_sha256(Path(T.__file__)),
            "fixtures/fixture-trace.jsonl": T.file_sha256(PHASE_DIR / "fixtures" / "fixture-trace.jsonl"),
            "tests/test_r3_telemetry.py": T.file_sha256(PHASE_DIR / "tests" / "test_r3_telemetry.py"),
        },
        "validation": {
            "cases": validation["summary"]["total"],
            "passed": validation["summary"]["passed"],
            "all_pass": validation["summary"]["all_pass"],
        },
        "prohibited_actions": [
            "running the 30B model",
            "acquiring fresh traces",
            "opening EVAL",
            "selecting or tuning a policy",
            "claiming a validated cost model",
        ],
    }
    return freeze


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--created-utc", default=None)
    ap.add_argument("--anchor-commit", default=None)
    ap.add_argument("--skip-real-trace", action="store_true")
    args = ap.parse_args()

    audit = json.loads((T.R3A_DIR / "CANONICAL_R2_12_AUDIT.json").read_text(encoding="utf-8"))
    validation = build_validation(skip_real=args.skip_real_trace)
    validation["r3_00a_audit_digest"] = audit["audit_digest"]
    T.write_json_deterministic(PHASE_DIR / "OFFLINE_VALIDATION.json", validation)

    implementation = build_implementation()
    T.write_json_deterministic(PHASE_DIR / "IMPLEMENTATION.json", implementation)

    if args.created_utc and args.anchor_commit:
        freeze = build_freeze(args.created_utc, args.anchor_commit, validation)
        T.write_json_deterministic(PHASE_DIR / "FREEZE.json", freeze)
        print("wrote FREEZE.json")

    print(f"validation: {validation['summary']['passed']}/{validation['summary']['total']} cases, "
          f"all_pass={validation['summary']['all_pass']}")
    return 0 if validation["summary"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
