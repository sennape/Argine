"""R2-07's prerequisites: R2-01 PASS (trace identity binding), R2-04
PASS/II-A-closed, R2-05 PASS, and R2-06 PASS -- verified explicitly,
every run, fail closed.

Reuses (never duplicates) `r2_06.prerequisite.require_r2_01_pass_and_r2_05_pass()`
for the R2-01/R2-04/R2-05 half of the chain. Adds, specific to R2-07:

  - confirming a R2-06 PASS run exists at all;
  - confirming that R2-06 PASS run's OWN recorded binding (r2_01_run_id)
    agrees with the CURRENT R2-01 binding (the same binding-consistency
    pattern every downstream experiment's prerequisite module uses for
    its upstream);
  - confirming the R2-01 PASS run's OWN recorded candidate_status is
    CANDIDATE (re-verified independently here too, exactly as
    r2_05's/r2_06's own prerequisite modules do, rather than trusting
    the chain's PASS outcomes alone to imply it).

No trace path, trace SHA-256, or run id is hard-coded anywhere in this
module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..r2_06 import prerequisite as r2_06_prerequisite

STATUS_OK = "OK"
STATUS_UPSTREAM_PREREQUISITE_FAILED = "UPSTREAM_PREREQUISITE_FAILED"
STATUS_NO_R2_06_RUN = "NO_R2_06_RUN"
STATUS_R2_06_NOT_PASS = "R2_06_NOT_PASS"
STATUS_R2_06_AMBIGUOUS_LATEST = "R2_06_AMBIGUOUS_LATEST"
STATUS_R2_06_EVIDENCE_MISSING = "R2_06_EVIDENCE_MISSING"
STATUS_BINDING_MISMATCH = "BINDING_MISMATCH"


@dataclass
class R207Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    r2_04_run_id: Optional[str] = None
    r2_05_run_id: Optional[str] = None
    r2_06_run_id: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None
    candidate_status: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _latest_r2_06_pass_entry(registry: List[dict]) -> "tuple[Optional[dict], Optional[str]]":
    passed = [e for e in registry
             if e.get("identity", {}).get("experiment_id") == "R2-06"
             and e.get("identity", {}).get("outcome") == "PASS"]
    if not passed:
        return None, None
    latest_ends = sorted({e["identity"]["end_utc"] for e in passed}, reverse=True)
    newest = [e for e in passed if e["identity"]["end_utc"] == latest_ends[0]]
    if len(newest) > 1:
        return None, (f"{len(newest)} R2-06 PASS runs share the latest end_utc "
                      f"({latest_ends[0]}); which one R2-07 should bind to is "
                      f"ambiguous and must not be guessed: "
                      f"{[e['identity']['run_id'] for e in newest]}")
    return newest[0], None


def require_full_chain_pass(root: Optional[Path] = None) -> R207Binding:
    """The fail-closed gate every R2-07 run passes through first."""
    upstream = r2_06_prerequisite.require_r2_01_pass_and_r2_05_pass(root)
    if not upstream.ok:
        return R207Binding(STATUS_UPSTREAM_PREREQUISITE_FAILED,
                           f"R2-01/R2-04/R2-05 prerequisite (reused from "
                           f"r2_06.prerequisite) not met: {upstream.message}")

    registry = store.load_registry(root)
    any_r2_06 = [e for e in registry if e.get("identity", {}).get("experiment_id") == "R2-06"]
    if not any_r2_06:
        return R207Binding(STATUS_NO_R2_06_RUN,
                           "no R2-06 run exists yet; R2-07 requires R2-06 (the second "
                           "II-B experiment) to have reached PASS before this, the "
                           "third II-B experiment, may run (run `argine.bat research2 "
                           "run R2-06` first).",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id,
                           trace_path=upstream.trace_path, trace_sha256=upstream.trace_sha256,
                           candidate_status=upstream.candidate_status)

    entry, ambiguity_message = _latest_r2_06_pass_entry(any_r2_06)
    if ambiguity_message is not None:
        return R207Binding(STATUS_R2_06_AMBIGUOUS_LATEST, ambiguity_message,
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id,
                           candidate_status=upstream.candidate_status)
    if entry is None:
        latest_outcome = sorted(any_r2_06, key=lambda e: e["identity"]["end_utc"])[-1][
            "identity"]["outcome"]
        return R207Binding(STATUS_R2_06_NOT_PASS,
                           f"R2-06 has not reached PASS (latest recorded outcome: "
                           f"{latest_outcome!r}); R2-07 refuses to run.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id,
                           candidate_status=upstream.candidate_status)

    run_dir = Path(entry["run_dir"])
    provenance_path = run_dir / "provenance.json"
    if not provenance_path.is_file():
        return R207Binding(STATUS_R2_06_EVIDENCE_MISSING,
                           f"R2-06 PASS run {entry['identity']['run_id']}'s own "
                           f"provenance.json evidence is missing at {run_dir}.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id,
                           r2_06_run_id=entry["identity"]["run_id"],
                           candidate_status=upstream.candidate_status)
    try:
        r2_06_prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        r2_06_r2_01_run_id = r2_06_prov["binding"]["r2_01_run_id"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R207Binding(STATUS_R2_06_EVIDENCE_MISSING,
                           f"R2-06 PASS run {entry['identity']['run_id']}'s "
                           f"provenance.json is unreadable or malformed: {e}",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id,
                           r2_06_run_id=entry["identity"]["run_id"],
                           candidate_status=upstream.candidate_status)

    if r2_06_r2_01_run_id != upstream.r2_01_run_id:
        return R207Binding(STATUS_BINDING_MISMATCH,
                           f"R2-06's own recorded binding (r2_01_run_id="
                           f"{r2_06_r2_01_run_id}) disagrees with the CURRENT R2-01 "
                           f"binding (r2_01_run_id={upstream.r2_01_run_id}); R2-07 "
                           f"refuses to build on an inconsistent evidence chain.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id,
                           r2_06_run_id=entry["identity"]["run_id"],
                           candidate_status=upstream.candidate_status)

    return R207Binding(STATUS_OK,
                       f"bound to R2-01 PASS run {upstream.r2_01_run_id} "
                       f"(candidate_status={upstream.candidate_status}), R2-04 PASS run "
                       f"{upstream.r2_04_run_id}, R2-05 PASS run {upstream.r2_05_run_id}, "
                       f"and R2-06 PASS run {entry['identity']['run_id']}; trace "
                       f"identity re-verified byte-identical on disk.",
                       r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                       r2_05_run_id=upstream.r2_05_run_id,
                       r2_06_run_id=entry["identity"]["run_id"],
                       trace_path=upstream.trace_path, trace_sha256=upstream.trace_sha256,
                       candidate_status=upstream.candidate_status)
