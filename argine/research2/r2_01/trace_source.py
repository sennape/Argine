"""R2-01 trace-source resolution (section 3 of the R2-01 task).

Precedence:
  1. explicit CLI/API `trace_source` argument;
  2. an explicit source previously pinned in Research2 local state
     (`store.set_experiment_state("R2-01", ..., extra={"trace_source": path})`);
  3. a documented canonical historical Research Part 1 trace -- ONLY if
     exactly one locally-present candidate is unambiguously documented as
     canonical. As of R2-01's own discovery (see docs/research2/METHODOLOGY.md
     and this experiment's README) no such single documented canonical trace
     exists: real local candidates are either explicitly non-publication-
     quality smoke runs, or an explicitly-labelled fixture, and the one real
     publication-quality run this repository's own documentation points to
     (EXP-019 windows-validation-2026-10-12-r2) is not present on this
     machine (its raw/ directory is empty; Research Part 1's README
     documents raw evidence as partly kept outside Git, consistent with it
     living only on external storage). This tier therefore deliberately
     never resolves to a single answer on this machine today; it exists so
     a future, explicitly documented canonical trace can be wired in later
     WITHOUT an API change;
  4. BLOCKED: TRACE_SOURCE_MISSING (nothing usable at all) or
     TRACE_SOURCE_AMBIGUOUS (more than one plausible candidate; never guess).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import trace_contract

TRACE_SOURCE_MISSING = "TRACE_SOURCE_MISSING"
TRACE_SOURCE_AMBIGUOUS = "TRACE_SOURCE_AMBIGUOUS"
TRACE_SOURCE_RESOLVED = "TRACE_SOURCE_RESOLVED"


@dataclass
class Candidate:
    path: str
    plausible: bool
    reason: str


@dataclass
class Resolution:
    status: str  # TRACE_SOURCE_RESOLVED | TRACE_SOURCE_MISSING | TRACE_SOURCE_AMBIGUOUS
    resolved_path: Optional[str] = None
    tier: Optional[str] = None  # "cli" | "local_state" | "documented_canonical"
    candidates: List[Candidate] = field(default_factory=list)
    message: str = ""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def discover_local_candidates(argine2_root: Optional[Path] = None) -> List[Candidate]:
    """Enumerate real (non-synthetic-test-fixture) trace files this machine
    can see, read-only, with an explicit plausibility note for each. Never
    used to silently auto-select -- only to report ambiguity precisely."""
    out: List[Candidate] = []
    root = argine2_root
    if root is None:
        sibling = _repo_root().parent / "Argine2"
        root = sibling if sibling.is_dir() else None
    if root is None:
        return out

    documented_quality_hints = {
        # path suffix (relative to the Argine2 root) -> (plausible, reason)
        "EXP-019-instrumentation-validation/processed/e2e-fixture/trace.jsonl.gz": (
            False, "Research Part 1's own README labels this the 'fixture / Linux "
                  "evidence' tier (a tiny synthetic 2-layer MoE, not a real "
                  "workload run); useful for format validation, not as "
                  "canonical Part 2 evidence"),
    }
    for exp019_dir in ("windows-validation-2026-10-12", "windows-validation-2026-10-12-r2"):
        p = (root / "experiments" / "EXP-019-instrumentation-validation" / "raw"
            / exp019_dir / "run-00-4G" / "trace.jsonl.gz")
        if p.exists() and p.stat().st_size > 0:
            out.append(Candidate(str(p), True,
                                 "Research Part 1's own README documents this as the "
                                 "publication-quality Windows replay-fidelity validation run"))
    for smoke_dir in ("SMOKE-30B", "SMOKE-30B-r2", "SMOKE-text-provenance",
                     "SMOKE-text-provenance-fixed"):
        p = root / "experiments" / smoke_dir / "run-00-4G" / "trace.jsonl.gz"
        if p.exists() and p.stat().st_size > 0:
            out.append(Candidate(str(p), False,
                                 f"{smoke_dir} is an informal smoke run; its own manifest "
                                 f"records publication_quality=false / runtime_tested=false"))
    fixture = (root / "experiments" / "EXP-019-instrumentation-validation" / "processed"
              / "e2e-fixture" / "trace.jsonl.gz")
    if fixture.exists() and fixture.stat().st_size > 0:
        plausible, reason = documented_quality_hints[
            "EXP-019-instrumentation-validation/processed/e2e-fixture/trace.jsonl.gz"]
        out.append(Candidate(str(fixture), plausible, reason))
    return out


def resolve(*, explicit_source: Optional[str] = None,
           local_state_source: Optional[str] = None,
           argine2_root: Optional[Path] = None) -> Resolution:
    if explicit_source:
        return Resolution(status=TRACE_SOURCE_RESOLVED, resolved_path=explicit_source,
                          tier="cli",
                          message=f"using explicitly provided trace source: {explicit_source}")
    if local_state_source:
        return Resolution(status=TRACE_SOURCE_RESOLVED, resolved_path=local_state_source,
                          tier="local_state",
                          message=f"using trace source pinned in Research2 local state: "
                                  f"{local_state_source}")

    candidates = discover_local_candidates(argine2_root)
    plausible = [c for c in candidates if c.plausible]
    if len(plausible) == 1:
        return Resolution(status=TRACE_SOURCE_RESOLVED, resolved_path=plausible[0].path,
                          tier="documented_canonical", candidates=candidates,
                          message=f"exactly one documented-canonical local candidate: "
                                  f"{plausible[0].path}")
    if not candidates:
        return Resolution(
            status=TRACE_SOURCE_MISSING, candidates=candidates,
            message="no usable local trace source found (checked EXP-019 raw/ Windows "
                    "validation runs, SMOKE-* runs, and the EXP-019 fixture). Pass "
                    "--trace-source PATH, or attach the real publication-quality trace "
                    "(see docs/research2/experiments/R2-01.../README.md).")
    return Resolution(
        status=TRACE_SOURCE_AMBIGUOUS, candidates=candidates,
        message=f"{len(candidates)} plausible local trace source(s) found and none is "
                f"unambiguously documented as canonical for Research Part 2 "
                f"({sum(1 for c in candidates if c.plausible)} marked plausible). "
                f"Re-run with --trace-source PATH naming the one to use.")
