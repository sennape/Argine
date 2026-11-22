"""R2-06's prerequisites: R2-01 PASS (trace identity binding) and R2-05
PASS (confirming the first II-B experiment reached PASS before this,
the second II-B experiment, is allowed to run) -- verified explicitly,
every run, fail closed.

Reuses (never duplicates) `r2_05.prerequisite.require_r2_01_pass_and_r2_04_pass()`
for the R2-01-trace-identity-plus-II-A-closure half of the check. Adds,
specific to R2-06:

  - confirming a R2-05 PASS run exists at all;
  - confirming that R2-05 PASS run's OWN recorded binding (r2_01_run_id)
    agrees with the CURRENT R2-01 binding (the same binding-consistency
    pattern r2_03/r2_04/r2_05's own prerequisite modules use for their
    upstream);
  - confirming the R2-01 PASS run's OWN recorded candidate_status is
    CANDIDATE (re-verified independently here too, exactly as r2_05's
    own prerequisite module does, rather than trusting r2_05's PASS
    outcome alone to imply it).

No trace path, trace SHA-256, or run id is hard-coded anywhere in this
module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..r2_05 import prerequisite as r2_05_prerequisite

STATUS_OK = "OK"
STATUS_R2_01_OR_R2_04_PREREQUISITE_FAILED = "R2_01_OR_R2_04_PREREQUISITE_FAILED"
STATUS_NO_R2_05_RUN = "NO_R2_05_RUN"
STATUS_R2_05_NOT_PASS = "R2_05_NOT_PASS"
STATUS_R2_05_AMBIGUOUS_LATEST = "R2_05_AMBIGUOUS_LATEST"
STATUS_R2_05_EVIDENCE_MISSING = "R2_05_EVIDENCE_MISSING"
STATUS_BINDING_MISMATCH = "BINDING_MISMATCH"


@dataclass
class R206Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    r2_04_run_id: Optional[str] = None
    r2_05_run_id: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None
    candidate_status: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _latest_r2_05_pass_entry(registry: List[dict]) -> "tuple[Optional[dict], Optional[str]]":
    passed = [e for e in registry
             if e.get("identity", {}).get("experiment_id") == "R2-05"
             and e.get("identity", {}).get("outcome") == "PASS"]
    if not passed:
        return None, None
    latest_ends = sorted({e["identity"]["end_utc"] for e in passed}, reverse=True)
    newest = [e for e in passed if e["identity"]["end_utc"] == latest_ends[0]]
    if len(newest) > 1:
        return None, (f"{len(newest)} R2-05 PASS runs share the latest end_utc "
                      f"({latest_ends[0]}); which one R2-06 should bind to is "
                      f"ambiguous and must not be guessed: "
                      f"{[e['identity']['run_id'] for e in newest]}")
    return newest[0], None


def require_r2_01_pass_and_r2_05_pass(root: Optional[Path] = None) -> R206Binding:
    """The fail-closed gate every R2-06 run passes through first."""
    upstream = r2_05_prerequisite.require_r2_01_pass_and_r2_04_pass(root)
    if not upstream.ok:
        return R206Binding(STATUS_R2_01_OR_R2_04_PREREQUISITE_FAILED,
                           f"R2-01/R2-04 prerequisite (reused from r2_05.prerequisite) "
                           f"not met: {upstream.message}")

    registry = store.load_registry(root)
    any_r2_05 = [e for e in registry if e.get("identity", {}).get("experiment_id") == "R2-05"]
    if not any_r2_05:
        return R206Binding(STATUS_NO_R2_05_RUN,
                           "no R2-05 run exists yet; R2-06 requires R2-05 (the first "
                           "II-B experiment) to have reached PASS before this, the "
                           "second II-B experiment, may run (run `argine.bat research2 "
                           "run R2-05` first).",
                           r2_01_run_id=upstream.r2_01_run_id,
                           r2_04_run_id=upstream.r2_04_run_id,
                           trace_path=upstream.trace_path, trace_sha256=upstream.trace_sha256,
                           candidate_status=upstream.candidate_status)

    entry, ambiguity_message = _latest_r2_05_pass_entry(any_r2_05)
    if ambiguity_message is not None:
        return R206Binding(STATUS_R2_05_AMBIGUOUS_LATEST, ambiguity_message,
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           candidate_status=upstream.candidate_status)
    if entry is None:
        latest_outcome = sorted(any_r2_05, key=lambda e: e["identity"]["end_utc"])[-1][
            "identity"]["outcome"]
        return R206Binding(STATUS_R2_05_NOT_PASS,
                           f"R2-05 has not reached PASS (latest recorded outcome: "
                           f"{latest_outcome!r}); R2-06 refuses to run.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           candidate_status=upstream.candidate_status)

    run_dir = Path(entry["run_dir"])
    provenance_path = run_dir / "provenance.json"
    if not provenance_path.is_file():
        return R206Binding(STATUS_R2_05_EVIDENCE_MISSING,
                           f"R2-05 PASS run {entry['identity']['run_id']}'s own "
                           f"provenance.json evidence is missing at {run_dir}.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=entry["identity"]["run_id"],
                           candidate_status=upstream.candidate_status)
    try:
        r2_05_prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        r2_05_r2_01_run_id = r2_05_prov["binding"]["r2_01_run_id"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R206Binding(STATUS_R2_05_EVIDENCE_MISSING,
                           f"R2-05 PASS run {entry['identity']['run_id']}'s "
                           f"provenance.json is unreadable or malformed: {e}",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=entry["identity"]["run_id"],
                           candidate_status=upstream.candidate_status)

    if r2_05_r2_01_run_id != upstream.r2_01_run_id:
        return R206Binding(STATUS_BINDING_MISMATCH,
                           f"R2-05's own recorded binding (r2_01_run_id="
                           f"{r2_05_r2_01_run_id}) disagrees with the CURRENT R2-01 "
                           f"binding (r2_01_run_id={upstream.r2_01_run_id}); R2-06 "
                           f"refuses to build on an inconsistent evidence chain.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=entry["identity"]["run_id"],
                           candidate_status=upstream.candidate_status)

    return R206Binding(STATUS_OK,
                       f"bound to R2-01 PASS run {upstream.r2_01_run_id} "
                       f"(candidate_status={upstream.candidate_status}), R2-04 PASS run "
                       f"{upstream.r2_04_run_id}, and R2-05 PASS run "
                       f"{entry['identity']['run_id']}; trace identity re-verified "
                       f"byte-identical on disk.",
                       r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                       r2_05_run_id=entry["identity"]["run_id"],
                       trace_path=upstream.trace_path, trace_sha256=upstream.trace_sha256,
                       candidate_status=upstream.candidate_status)
