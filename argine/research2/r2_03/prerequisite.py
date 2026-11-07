"""R2-03's prerequisites: R2-01 PASS, R2-02 PASS, and trace-identity binding
consistent between the two -- all three verified explicitly, every run,
fail closed.

Reuses (never duplicates) `r2_02.prerequisite.require_r2_01_pass()` for
the R2-01 half of the check -- that function already re-hashes the trace
R2-01's own latest PASS run validated and fails closed on any drift. This
module adds the two things that are specific to R2-03: confirming R2-02
itself has reached PASS, and confirming R2-02's own recorded trace binding
agrees with R2-01's current one (so R2-03 is provably building on the same
validated evidence chain, not a R2-01 re-run that happened to diverge from
what R2-02 actually evaluated).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..r2_02 import prerequisite as r2_02_prerequisite

STATUS_OK = "OK"
STATUS_R2_01_PREREQUISITE_FAILED = "R2_01_PREREQUISITE_FAILED"
STATUS_NO_R2_02_RUN = "NO_R2_02_RUN"
STATUS_R2_02_NOT_PASS = "R2_02_NOT_PASS"
STATUS_R2_02_AMBIGUOUS_LATEST = "R2_02_AMBIGUOUS_LATEST"
STATUS_R2_02_EVIDENCE_MISSING = "R2_02_EVIDENCE_MISSING"
STATUS_BINDING_MISMATCH = "BINDING_MISMATCH"


@dataclass
class R203Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    r2_02_run_id: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _latest_r2_02_pass_entry(registry: List[dict]) -> "tuple[Optional[dict], Optional[str]]":
    passed = [e for e in registry
             if e.get("identity", {}).get("experiment_id") == "R2-02"
             and e.get("identity", {}).get("outcome") == "PASS"]
    if not passed:
        return None, None
    latest_ends = sorted({e["identity"]["end_utc"] for e in passed}, reverse=True)
    newest = [e for e in passed if e["identity"]["end_utc"] == latest_ends[0]]
    if len(newest) > 1:
        return None, (f"{len(newest)} R2-02 PASS runs share the latest end_utc "
                      f"({latest_ends[0]}); which one R2-03 should bind to is "
                      f"ambiguous and must not be guessed: "
                      f"{[e['identity']['run_id'] for e in newest]}")
    return newest[0], None


def require_r2_01_and_r2_02_pass(root: Optional[Path] = None) -> R203Binding:
    """The fail-closed gate every R2-03 run passes through first."""
    r2_01_binding = r2_02_prerequisite.require_r2_01_pass(root)
    if not r2_01_binding.ok:
        return R203Binding(STATUS_R2_01_PREREQUISITE_FAILED,
                           f"R2-01 prerequisite not met: {r2_01_binding.message}")

    registry = store.load_registry(root)
    any_r2_02 = [e for e in registry if e.get("identity", {}).get("experiment_id") == "R2-02"]
    if not any_r2_02:
        return R203Binding(STATUS_NO_R2_02_RUN,
                           "no R2-02 run exists yet; R2-03 requires a PASS R2-02 run as "
                           "its upstream prerequisite (run `argine.bat research2 run "
                           "R2-02` first).",
                           r2_01_run_id=r2_01_binding.r2_01_run_id,
                           trace_path=r2_01_binding.trace_path,
                           trace_sha256=r2_01_binding.trace_sha256)

    entry, ambiguity_message = _latest_r2_02_pass_entry(any_r2_02)
    if ambiguity_message is not None:
        return R203Binding(STATUS_R2_02_AMBIGUOUS_LATEST, ambiguity_message,
                           r2_01_run_id=r2_01_binding.r2_01_run_id)
    if entry is None:
        latest_outcome = sorted(any_r2_02, key=lambda e: e["identity"]["end_utc"])[-1][
            "identity"]["outcome"]
        return R203Binding(STATUS_R2_02_NOT_PASS,
                           f"R2-02 has not reached PASS (latest recorded outcome: "
                           f"{latest_outcome!r}); R2-03 refuses to run against a "
                           f"non-PASS R2-02 result.",
                           r2_01_run_id=r2_01_binding.r2_01_run_id)

    run_dir = Path(entry["run_dir"])
    provenance_path = run_dir / "provenance.json"
    if not provenance_path.is_file():
        return R203Binding(STATUS_R2_02_EVIDENCE_MISSING,
                           f"R2-02 PASS run {entry['identity']['run_id']}'s own "
                           f"provenance.json evidence is missing at {run_dir}.",
                           r2_01_run_id=r2_01_binding.r2_01_run_id,
                           r2_02_run_id=entry["identity"]["run_id"])

    import json
    try:
        r2_02_prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        r2_02_sha = r2_02_prov["r2_01_binding"]["trace_sha256"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R203Binding(STATUS_R2_02_EVIDENCE_MISSING,
                           f"R2-02 PASS run {entry['identity']['run_id']}'s "
                           f"provenance.json is unreadable or malformed: {e}",
                           r2_01_run_id=r2_01_binding.r2_01_run_id,
                           r2_02_run_id=entry["identity"]["run_id"])

    if r2_02_sha != r2_01_binding.trace_sha256:
        return R203Binding(STATUS_BINDING_MISMATCH,
                           f"R2-02's own recorded trace binding (sha256={r2_02_sha}) "
                           f"disagrees with R2-01's CURRENT validated trace "
                           f"(sha256={r2_01_binding.trace_sha256}); R2-03 refuses to "
                           f"build on an inconsistent evidence chain.",
                           r2_01_run_id=r2_01_binding.r2_01_run_id,
                           r2_02_run_id=entry["identity"]["run_id"])

    return R203Binding(STATUS_OK,
                       f"bound to R2-01 run {r2_01_binding.r2_01_run_id} and R2-02 PASS "
                       f"run {entry['identity']['run_id']}, trace identity consistent "
                       f"across both.",
                       r2_01_run_id=r2_01_binding.r2_01_run_id,
                       r2_02_run_id=entry["identity"]["run_id"],
                       trace_path=r2_01_binding.trace_path,
                       trace_sha256=r2_01_binding.trace_sha256)
