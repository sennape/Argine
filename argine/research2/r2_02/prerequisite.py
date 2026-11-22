"""R2-02 section 5: the R2-01 PASS prerequisite and trace-SHA binding.

R2-02 must not run at all unless R2-01 has a canonical PASS run on record,
and must bind its own evidence to the EXACT trace file that PASS run
validated -- re-hashed now, not merely trusted from the old record, so a
trace that has changed on disk since that PASS run fails closed rather
than being silently accepted as if nothing had changed.

No trace SHA-256 is hard-coded anywhere in this module: the "validated
trace" is whichever one R2-01's own latest PASS run actually bound to
(dynamically, from that run's own evidence), never a literal string that
would ossify around today's one historical run and silently stop
protecting a future one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..r2_01 import trace_contract

STATUS_OK = "OK"
STATUS_NO_R2_01_RUN = "NO_R2_01_RUN"
STATUS_R2_01_NOT_PASS = "R2_01_NOT_PASS"
STATUS_R2_01_EVIDENCE_MISSING = "R2_01_EVIDENCE_MISSING"
STATUS_TRACE_CHANGED = "TRACE_CHANGED"
STATUS_TRACE_MISSING = "TRACE_MISSING"
STATUS_AMBIGUOUS_LATEST = "AMBIGUOUS_LATEST"


class PrerequisiteError(ValueError):
    pass


@dataclass
class R201Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None
    trace_byte_size: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _latest_r2_01_pass_entry(registry: List[dict]) -> "tuple[Optional[dict], Optional[str]]":
    """Mirrors r2_01.orchestrator.analyse()'s own latest-entry selection
    (reused logic, not a new policy): among R2-01 registry entries with
    outcome PASS, the one with the latest end_utc; ambiguous if more than
    one PASS run shares that latest end_utc."""
    passed = [e for e in registry
             if e.get("identity", {}).get("experiment_id") == "R2-01"
             and e.get("identity", {}).get("outcome") == "PASS"]
    if not passed:
        return None, None
    latest_ends = sorted({e["identity"]["end_utc"] for e in passed}, reverse=True)
    newest = [e for e in passed if e["identity"]["end_utc"] == latest_ends[0]]
    if len(newest) > 1:
        return None, (f"{len(newest)} R2-01 PASS runs share the latest end_utc "
                      f"({latest_ends[0]}); which one R2-02 should bind to is "
                      f"ambiguous and must not be guessed: "
                      f"{[e['identity']['run_id'] for e in newest]}")
    return newest[0], None


def require_r2_01_pass(root: Optional[Path] = None) -> R201Binding:
    """The fail-closed gate every R2-02 run must pass through before any
    replay happens. Reads R2-01's OWN registry/evidence (read-only; this
    never touches R2-01's state) and re-hashes the trace file R2-01's
    latest PASS run actually validated."""
    registry = store.load_registry(root)
    any_r2_01 = [e for e in registry if e.get("identity", {}).get("experiment_id") == "R2-01"]
    if not any_r2_01:
        return R201Binding(STATUS_NO_R2_01_RUN,
                           "no R2-01 run exists yet; R2-02 requires a PASS R2-01 run "
                           "as its upstream prerequisite (run `argine.bat research2 "
                           "run R2-01` first).")

    entry, ambiguity_message = _latest_r2_01_pass_entry(any_r2_01)
    if ambiguity_message is not None:
        return R201Binding(STATUS_AMBIGUOUS_LATEST, ambiguity_message)
    if entry is None:
        latest_outcome = sorted(any_r2_01, key=lambda e: e["identity"]["end_utc"])[-1][
            "identity"]["outcome"]
        return R201Binding(STATUS_R2_01_NOT_PASS,
                           f"R2-01 has not reached PASS (latest recorded outcome: "
                           f"{latest_outcome!r}); R2-02 refuses to run against a "
                           f"non-PASS or still-caveated R2-01 result.")

    run_dir = Path(entry["run_dir"])
    source_trace_path = run_dir / "source-trace.json"
    if not source_trace_path.is_file():
        return R201Binding(STATUS_R2_01_EVIDENCE_MISSING,
                           f"R2-01 PASS run {entry['identity']['run_id']}'s own "
                           f"source-trace.json evidence is missing at {run_dir}; "
                           f"cannot establish what trace it validated.",
                           r2_01_run_id=entry["identity"]["run_id"])
    try:
        recorded = json.loads(source_trace_path.read_text(encoding="utf-8"))
        recorded_source = recorded["source"]
        recorded_path = Path(recorded_source["path"])
        recorded_sha = recorded_source["sha256"]
        recorded_size = recorded_source["byte_size"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R201Binding(STATUS_R2_01_EVIDENCE_MISSING,
                           f"R2-01 PASS run {entry['identity']['run_id']}'s "
                           f"source-trace.json is unreadable or malformed: {e}",
                           r2_01_run_id=entry["identity"]["run_id"])

    if not recorded_path.is_file():
        return R201Binding(STATUS_TRACE_MISSING,
                           f"the trace file R2-01's PASS run {entry['identity']['run_id']} "
                           f"validated ({recorded_path}) no longer exists on disk; R2-02 "
                           f"refuses to silently fall back to a different trace.",
                           r2_01_run_id=entry["identity"]["run_id"], trace_path=str(recorded_path))

    now_hashed = trace_contract.sha256_file(recorded_path)
    if now_hashed["sha256"] != recorded_sha or now_hashed["byte_size"] != recorded_size:
        return R201Binding(STATUS_TRACE_CHANGED,
                           f"the trace file at {recorded_path} has changed since R2-01's "
                           f"PASS run {entry['identity']['run_id']} validated it (recorded "
                           f"sha256={recorded_sha} size={recorded_size}; now "
                           f"sha256={now_hashed['sha256']} size={now_hashed['byte_size']}); "
                           f"R2-02 fails closed rather than silently evaluating a different "
                           f"trace under the same path. A changed source requires explicit, "
                           f"scientifically approved re-validation of R2-01 first.",
                           r2_01_run_id=entry["identity"]["run_id"], trace_path=str(recorded_path),
                           trace_sha256=recorded_sha, trace_byte_size=recorded_size)

    return R201Binding(STATUS_OK,
                       f"bound to the trace R2-01's PASS run {entry['identity']['run_id']} "
                       f"validated, re-verified byte-identical on disk.",
                       r2_01_run_id=entry["identity"]["run_id"], trace_path=str(recorded_path),
                       trace_sha256=recorded_sha, trace_byte_size=recorded_size)
