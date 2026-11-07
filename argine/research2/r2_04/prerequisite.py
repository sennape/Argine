"""R2-04's prerequisites: R2-01 PASS, R2-02 PASS, R2-03 PASS, and trace-
identity + binding consistency across all three -- verified explicitly,
every run, fail closed.

Reuses (never duplicates) `r2_03.prerequisite.require_r2_01_and_r2_02_pass()`
for the R2-01+R2-02 half of the check. This module adds the layer that is
specific to R2-04: confirming R2-03 itself has reached PASS, confirming
R2-03's own recorded trace binding agrees with R2-01's current one, and
confirming R2-03's OWN internal R2-02-reproduction check still reads OK in
its registered evidence (R2-04 must never synthesize over an R2-03 run
whose own reproduction guarantee has since been invalidated).

No metric value, budget, or policy definition is hard-coded here: R2-04
binds to whatever R2-03's own latest PASS run actually recorded (its
provenance.json's `fixed_budgets_bytes` and the binding's run ids), not to
today's `r2_03.budget_sweep` module constants, in case they ever diverge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..r2_03 import prerequisite as r2_03_prerequisite

STATUS_OK = "OK"
STATUS_R2_01_OR_R2_02_PREREQUISITE_FAILED = "R2_01_OR_R2_02_PREREQUISITE_FAILED"
STATUS_NO_R2_03_RUN = "NO_R2_03_RUN"
STATUS_R2_03_NOT_PASS = "R2_03_NOT_PASS"
STATUS_R2_03_AMBIGUOUS_LATEST = "R2_03_AMBIGUOUS_LATEST"
STATUS_R2_03_EVIDENCE_MISSING = "R2_03_EVIDENCE_MISSING"
STATUS_BINDING_MISMATCH = "BINDING_MISMATCH"
STATUS_R2_03_REPRODUCTION_INVALID = "R2_03_REPRODUCTION_INVALID"


@dataclass
class R204Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    r2_02_run_id: Optional[str] = None
    r2_03_run_id: Optional[str] = None
    r2_03_run_dir: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None
    fixed_budgets_bytes: Optional[List[int]] = None
    reproduction_budget_bytes: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _latest_r2_03_pass_entry(registry: List[dict]) -> "tuple[Optional[dict], Optional[str]]":
    passed = [e for e in registry
             if e.get("identity", {}).get("experiment_id") == "R2-03"
             and e.get("identity", {}).get("outcome") == "PASS"]
    if not passed:
        return None, None
    latest_ends = sorted({e["identity"]["end_utc"] for e in passed}, reverse=True)
    newest = [e for e in passed if e["identity"]["end_utc"] == latest_ends[0]]
    if len(newest) > 1:
        return None, (f"{len(newest)} R2-03 PASS runs share the latest end_utc "
                      f"({latest_ends[0]}); which one R2-04 should bind to is "
                      f"ambiguous and must not be guessed: "
                      f"{[e['identity']['run_id'] for e in newest]}")
    return newest[0], None


def require_r2_01_r2_02_r2_03_pass(root: Optional[Path] = None) -> R204Binding:
    """The fail-closed gate every R2-04 run passes through first."""
    r203_binding = r2_03_prerequisite.require_r2_01_and_r2_02_pass(root)
    if not r203_binding.ok:
        return R204Binding(STATUS_R2_01_OR_R2_02_PREREQUISITE_FAILED,
                           f"R2-01/R2-02 prerequisite not met: {r203_binding.message}")

    registry = store.load_registry(root)
    any_r2_03 = [e for e in registry if e.get("identity", {}).get("experiment_id") == "R2-03"]
    if not any_r2_03:
        return R204Binding(STATUS_NO_R2_03_RUN,
                           "no R2-03 run exists yet; R2-04 requires a PASS R2-03 run as "
                           "its upstream prerequisite (run `argine.bat research2 run "
                           "R2-03` first).",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id,
                           trace_path=r203_binding.trace_path,
                           trace_sha256=r203_binding.trace_sha256)

    entry, ambiguity_message = _latest_r2_03_pass_entry(any_r2_03)
    if ambiguity_message is not None:
        return R204Binding(STATUS_R2_03_AMBIGUOUS_LATEST, ambiguity_message,
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id)
    if entry is None:
        latest_outcome = sorted(any_r2_03, key=lambda e: e["identity"]["end_utc"])[-1][
            "identity"]["outcome"]
        return R204Binding(STATUS_R2_03_NOT_PASS,
                           f"R2-03 has not reached PASS (latest recorded outcome: "
                           f"{latest_outcome!r}); R2-04 refuses to synthesize over a "
                           f"non-PASS R2-03 result.",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id)

    run_dir = Path(entry["run_dir"])
    provenance_path = run_dir / "provenance.json"
    comparison_path = run_dir / "comparison.json"
    manifest_path = run_dir / "manifest.json"
    if not provenance_path.is_file() or not comparison_path.is_file() or not manifest_path.is_file():
        return R204Binding(STATUS_R2_03_EVIDENCE_MISSING,
                           f"R2-03 PASS run {entry['identity']['run_id']}'s own "
                           f"provenance.json/comparison.json/manifest.json evidence is "
                           f"missing at {run_dir}.",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id,
                           r2_03_run_id=entry["identity"]["run_id"])

    try:
        r2_03_prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        r2_03_sha = r2_03_prov["binding"]["trace_sha256"]
        r2_03_r2_01_run_id = r2_03_prov["binding"]["r2_01_run_id"]
        r2_03_r2_02_run_id = r2_03_prov["binding"]["r2_02_run_id"]
        fixed_budgets_bytes = list(r2_03_prov["fixed_budgets_bytes"])
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R204Binding(STATUS_R2_03_EVIDENCE_MISSING,
                           f"R2-03 PASS run {entry['identity']['run_id']}'s "
                           f"provenance.json is unreadable or malformed: {e}",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id,
                           r2_03_run_id=entry["identity"]["run_id"])

    if (r2_03_sha != r203_binding.trace_sha256
           or r2_03_r2_01_run_id != r203_binding.r2_01_run_id
           or r2_03_r2_02_run_id != r203_binding.r2_02_run_id):
        return R204Binding(STATUS_BINDING_MISMATCH,
                           f"R2-03's own recorded binding (trace_sha256={r2_03_sha}, "
                           f"r2_01_run_id={r2_03_r2_01_run_id}, "
                           f"r2_02_run_id={r2_03_r2_02_run_id}) disagrees with the "
                           f"CURRENT R2-01/R2-02 binding (trace_sha256="
                           f"{r203_binding.trace_sha256}, r2_01_run_id="
                           f"{r203_binding.r2_01_run_id}, r2_02_run_id="
                           f"{r203_binding.r2_02_run_id}); R2-04 refuses to build on "
                           f"an inconsistent evidence chain.",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id,
                           r2_03_run_id=entry["identity"]["run_id"])

    try:
        r2_03_comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        repro_check = r2_03_comparison.get("reproduction_check")
        r2_03_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        reproduction_budget_bytes = r2_03_manifest["experiment"]["parameters"][
            "reproduction_budget_bytes"]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        return R204Binding(STATUS_R2_03_EVIDENCE_MISSING,
                           f"R2-03 PASS run {entry['identity']['run_id']}'s "
                           f"comparison.json is unreadable or malformed: {e}",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id,
                           r2_03_run_id=entry["identity"]["run_id"])

    if not repro_check or not repro_check.get("ok"):
        return R204Binding(STATUS_R2_03_REPRODUCTION_INVALID,
                           f"R2-03 PASS run {entry['identity']['run_id']}'s own "
                           f"registered R2-02-reproduction check does not read OK in "
                           f"its evidence ({repro_check!r}); R2-04 refuses to "
                           f"synthesize over an R2-03 result whose own reproduction "
                           f"guarantee is not currently intact.",
                           r2_01_run_id=r203_binding.r2_01_run_id,
                           r2_02_run_id=r203_binding.r2_02_run_id,
                           r2_03_run_id=entry["identity"]["run_id"])

    return R204Binding(STATUS_OK,
                       f"bound to R2-01 run {r203_binding.r2_01_run_id}, R2-02 PASS run "
                       f"{r203_binding.r2_02_run_id}, and R2-03 PASS run "
                       f"{entry['identity']['run_id']}; trace identity and binding "
                       f"consistent across all three, R2-03's own R2-02-reproduction "
                       f"check reads OK.",
                       r2_01_run_id=r203_binding.r2_01_run_id,
                       r2_02_run_id=r203_binding.r2_02_run_id,
                       r2_03_run_id=entry["identity"]["run_id"],
                       r2_03_run_dir=str(run_dir),
                       trace_path=r203_binding.trace_path,
                       trace_sha256=r203_binding.trace_sha256,
                       fixed_budgets_bytes=fixed_budgets_bytes,
                       reproduction_budget_bytes=reproduction_budget_bytes)
