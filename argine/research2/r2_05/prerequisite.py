"""R2-05's prerequisites: R2-01 PASS (trace identity binding) and R2-04
PASS (confirming Research II-A is CLOSED/PASS before any II-B experiment
is allowed to run) -- verified explicitly, every run, fail closed.

Reuses (never duplicates) `r2_02.prerequisite.require_r2_01_pass()` for
the R2-01 trace-identity half of the check (re-hashes the trace file on
disk; refuses if it has changed since R2-01's PASS run validated it).
Adds, specific to R2-05:

  - confirming a R2-04 PASS run exists at all (II-A closure gate: R2-05
    is the first II-B experiment and must not run against an II-A that
    has not reached PASS);
  - confirming that R2-04 PASS run's OWN recorded r2_01_run_id agrees
    with the CURRENT R2-01 binding (the same binding-consistency pattern
    r2_03/r2_04's own prerequisite modules use for their upstream);
  - confirming the R2-01 PASS run's OWN recorded candidate_status is
    CANDIDATE (not NON-CANDIDATE/REFUSED) -- R2-05 refuses to bind to
    historical evidence that was itself recorded under a dirty/unverified
    tree, even if that run's scientific outcome was PASS.

No trace path, trace SHA-256, or run id is hard-coded anywhere in this
module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..r2_02 import prerequisite as r2_02_prerequisite

STATUS_OK = "OK"
STATUS_R2_01_PREREQUISITE_FAILED = "R2_01_PREREQUISITE_FAILED"
STATUS_R2_01_NOT_CANDIDATE = "R2_01_NOT_CANDIDATE"
STATUS_NO_R2_04_RUN = "NO_R2_04_RUN"
STATUS_R2_04_NOT_PASS = "R2_04_NOT_PASS"
STATUS_R2_04_AMBIGUOUS_LATEST = "R2_04_AMBIGUOUS_LATEST"
STATUS_R2_04_EVIDENCE_MISSING = "R2_04_EVIDENCE_MISSING"
STATUS_BINDING_MISMATCH = "BINDING_MISMATCH"


@dataclass
class R205Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    r2_04_run_id: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None
    candidate_status: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _latest_r2_04_pass_entry(registry: List[dict]) -> "tuple[Optional[dict], Optional[str]]":
    passed = [e for e in registry
             if e.get("identity", {}).get("experiment_id") == "R2-04"
             and e.get("identity", {}).get("outcome") == "PASS"]
    if not passed:
        return None, None
    latest_ends = sorted({e["identity"]["end_utc"] for e in passed}, reverse=True)
    newest = [e for e in passed if e["identity"]["end_utc"] == latest_ends[0]]
    if len(newest) > 1:
        return None, (f"{len(newest)} R2-04 PASS runs share the latest end_utc "
                      f"({latest_ends[0]}); which one R2-05 should bind to is "
                      f"ambiguous and must not be guessed: "
                      f"{[e['identity']['run_id'] for e in newest]}")
    return newest[0], None


def _r2_01_candidate_status(r2_01_run_id: str, root: Optional[Path] = None) -> Optional[str]:
    registry = store.load_registry(root)
    matches = [e for e in registry
              if e.get("identity", {}).get("experiment_id") == "R2-01"
              and e.get("identity", {}).get("run_id") == r2_01_run_id]
    if not matches:
        return None
    run_dir = Path(matches[0]["run_dir"])
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest.get("git", {}).get("candidate_status")
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def require_r2_01_pass_and_r2_04_pass(root: Optional[Path] = None) -> R205Binding:
    """The fail-closed gate every R2-05 run passes through first."""
    r201_binding = r2_02_prerequisite.require_r2_01_pass(root)
    if not r201_binding.ok:
        return R205Binding(STATUS_R2_01_PREREQUISITE_FAILED,
                           f"R2-01 prerequisite not met: {r201_binding.message}")

    candidate_status = _r2_01_candidate_status(r201_binding.r2_01_run_id, root)
    if candidate_status != "CANDIDATE":
        return R205Binding(
            STATUS_R2_01_NOT_CANDIDATE,
            f"R2-01 PASS run {r201_binding.r2_01_run_id}'s own recorded "
            f"candidate_status is {candidate_status!r}, not 'CANDIDATE'; R2-05 "
            f"refuses to bind to historical evidence that was not itself recorded "
            f"as candidate science, even though its scientific outcome was PASS.",
            r2_01_run_id=r201_binding.r2_01_run_id, trace_path=r201_binding.trace_path,
            trace_sha256=r201_binding.trace_sha256, candidate_status=candidate_status)

    registry = store.load_registry(root)
    any_r2_04 = [e for e in registry if e.get("identity", {}).get("experiment_id") == "R2-04"]
    if not any_r2_04:
        return R205Binding(STATUS_NO_R2_04_RUN,
                           "no R2-04 run exists yet; R2-05 requires Research II-A to be "
                           "CLOSED/PASS (a PASS R2-04 run) before any II-B experiment "
                           "may run (run `argine.bat research2 run R2-04` first).",
                           r2_01_run_id=r201_binding.r2_01_run_id,
                           trace_path=r201_binding.trace_path,
                           trace_sha256=r201_binding.trace_sha256,
                           candidate_status=candidate_status)

    entry, ambiguity_message = _latest_r2_04_pass_entry(any_r2_04)
    if ambiguity_message is not None:
        return R205Binding(STATUS_R2_04_AMBIGUOUS_LATEST, ambiguity_message,
                           r2_01_run_id=r201_binding.r2_01_run_id,
                           candidate_status=candidate_status)
    if entry is None:
        latest_outcome = sorted(any_r2_04, key=lambda e: e["identity"]["end_utc"])[-1][
            "identity"]["outcome"]
        return R205Binding(STATUS_R2_04_NOT_PASS,
                           f"R2-04 has not reached PASS (latest recorded outcome: "
                           f"{latest_outcome!r}); Research II-A is not CLOSED/PASS, so "
                           f"R2-05 refuses to run.",
                           r2_01_run_id=r201_binding.r2_01_run_id,
                           candidate_status=candidate_status)

    run_dir = Path(entry["run_dir"])
    provenance_path = run_dir / "provenance.json"
    if not provenance_path.is_file():
        return R205Binding(STATUS_R2_04_EVIDENCE_MISSING,
                           f"R2-04 PASS run {entry['identity']['run_id']}'s own "
                           f"provenance.json evidence is missing at {run_dir}.",
                           r2_01_run_id=r201_binding.r2_01_run_id,
                           r2_04_run_id=entry["identity"]["run_id"],
                           candidate_status=candidate_status)
    try:
        r2_04_prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        r2_04_r2_01_run_id = r2_04_prov["binding"]["r2_01_run_id"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R205Binding(STATUS_R2_04_EVIDENCE_MISSING,
                           f"R2-04 PASS run {entry['identity']['run_id']}'s "
                           f"provenance.json is unreadable or malformed: {e}",
                           r2_01_run_id=r201_binding.r2_01_run_id,
                           r2_04_run_id=entry["identity"]["run_id"],
                           candidate_status=candidate_status)

    if r2_04_r2_01_run_id != r201_binding.r2_01_run_id:
        return R205Binding(STATUS_BINDING_MISMATCH,
                           f"R2-04's own recorded binding (r2_01_run_id="
                           f"{r2_04_r2_01_run_id}) disagrees with the CURRENT R2-01 "
                           f"binding (r2_01_run_id={r201_binding.r2_01_run_id}); R2-05 "
                           f"refuses to build on an inconsistent evidence chain.",
                           r2_01_run_id=r201_binding.r2_01_run_id,
                           r2_04_run_id=entry["identity"]["run_id"],
                           candidate_status=candidate_status)

    return R205Binding(STATUS_OK,
                       f"bound to R2-01 PASS run {r201_binding.r2_01_run_id} "
                       f"(candidate_status={candidate_status}) and R2-04 PASS run "
                       f"{entry['identity']['run_id']} (Research II-A CLOSED/PASS); "
                       f"trace identity re-verified byte-identical on disk.",
                       r2_01_run_id=r201_binding.r2_01_run_id,
                       r2_04_run_id=entry["identity"]["run_id"],
                       trace_path=r201_binding.trace_path,
                       trace_sha256=r201_binding.trace_sha256,
                       candidate_status=candidate_status)
