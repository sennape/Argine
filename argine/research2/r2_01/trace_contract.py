"""R2Trace v1: the canonical, versioned trace contract for Research Part 2.

R2Trace v1 does NOT reinvent Research Part 1's trace representation. It
REUSES it: `argine.research.trace.Trace` ("argine-router-trace" v1, see that
module's docstring) already preserves exactly the semantic unit this
contract needs -- one `Step` is one forward pass (one llama.cpp graph / one
ubatch), `Step.routes[layer]` keeps the per-token list of selected-expert
tuples exactly as recorded, and `Step.experts(layer)` takes the UNION across
tokens only at the point residency is evaluated ("event", in the existing
event-atomic contract of replay.py/policies.py). R2-01's own evidence (see
docs/research2/METHODOLOGY.md) is that this is the correct unit: Research
Part 1 already ships the event-atomic contract and 18 fidelity regression
tests (tests/test_fidelity.py) pinning it, including byte-based eviction,
unequal expert sizes, exact LRU ordering, warm/cold start, and determinism.
Reimplementing any of that here would be exactly the "second divergent
implementation" R2-01 is told not to create.

What R2Trace v1 ADDS on top of Research Part 1's Trace format is the
provenance/normalization envelope Research Part 2 needs: source identity
(hash, size, format), explicit reconciliation counts (so silent row loss is
structurally impossible to miss), and an explicit statement of what this
contract does and does not claim about routing-order semantics, capacity
units and initial residency -- the open questions R2-01 exists to answer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...research.trace import Trace, TraceError

SCHEMA_VERSION = 1
SCHEMA_NAME = "r2trace"

# -- explicit, documented semantic decisions (section 5/6/7 of the R2-01 task) --

ROUTING_STEP_UNIT = (
    "one Research Part 1 Trace `Step` == one forward pass (one llama.cpp graph / "
    "one ubatch). This is the routing-step unit for R2Trace v1. During "
    "generation a step carries exactly one token; during prompt processing a "
    "step may carry many tokens whose required experts must be resident "
    "SIMULTANEOUSLY (the runtime pins them together). Flattening a step into "
    "independent per-token accesses would misrepresent that simultaneity, so "
    "R2Trace v1 preserves the step boundary and, within it, the per-token "
    "tuple grouping exactly as recorded (Step.routes[layer]); it does not "
    "flatten them."
)

EXPERT_ORDER_SEMANTICS = (
    "Within one token's selection tuple, expert order is recorded exactly as "
    "llama.cpp's router produced it, but is NOT claimed to be semantically "
    "meaningful (e.g. not a ranked preference order) by this contract. The "
    "quantity that IS semantically meaningful for residency/replay is the "
    "per-layer UNION of experts needed across every token of the step "
    "(Step.experts(layer) in Research Part 1's Trace, 'the event'), because "
    "that is what the real runtime must hold resident at once. R2Trace v1 "
    "records the full per-token tuples (so no information is discarded) and "
    "explicitly derives the per-step-per-layer union only at replay time, "
    "never earlier."
)

CAPACITY_SEMANTICS = (
    "Capacity is byte-budgeted: the unit of residency is one expert of one "
    "layer, sized by trace.meta.expert_bytes[layer] (Research Part 1's "
    "existing convention; see policies.Context.size / bytes_of). Expert "
    "sizes are NOT assumed uniform across layers (Research Part 1's own "
    "fidelity tests exercise unequal per-layer sizes). When a trace source "
    "carries no expert_bytes for one or more layers (observed for at least "
    "one real local candidate, SMOKE-30B -- see trace_source.py discovery "
    "notes), R2Trace v1 does NOT silently assume equal sizing: normalization "
    "records which layers are missing byte metadata, and replay for those "
    "layers falls back to Research Part 1's own existing fallback (count-"
    "based, 1 unit per expert) with that fallback explicitly flagged in the "
    "normalization report, never hidden."
)

INITIAL_RESIDENCY_SEMANTICS = (
    "Canonical cold start: replay begins with EVERY layer's resident set "
    "empty (Research Part 1's Policy.start() convention; see "
    "argine.research.policies.Policy). This is not claimed to reproduce a "
    "warm real-runtime state. Research Part 1 amortises this by recording "
    "'warmup' segment steps in the trace itself and serving (but not "
    "counting) them before any counted step -- the same convention R2Trace "
    "v1 replay reuses (argine.research.replay.served_steps / counted_step). "
    "A trace with no explicit warmup segment is recorded as "
    "'initial_residency: unknown_cold_assumed' rather than silently treated "
    "as equivalent to one that does warm up first."
)


class ContractError(ValueError):
    """A source trace does not satisfy the R2Trace v1 contract."""


class ExclusionCategory:
    MALFORMED_ROW = "malformed_row"
    EMPTY_ROUTES = "empty_routes_all_layers"


@dataclass
class SourceIdentity:
    path: str
    sha256: str
    byte_size: int
    source_format: str
    source_experiment: Optional[str] = None
    source_workload: Optional[str] = None
    source_split: Optional[str] = None


@dataclass
class NormalizationReport:
    schema_version: int
    source: SourceIdentity
    source_row_count: int
    normalized_step_count: int
    normalized_routing_event_count: int  # sum over steps of (layers touched)
    excluded_row_count: int
    exclusion_reasons: Dict[str, int]
    layers_missing_expert_bytes: List[int]
    initial_residency: str  # "unknown_cold_assumed" | "cold_start_documented"
    routing_step_unit: str = ROUTING_STEP_UNIT
    expert_order_semantics: str = EXPERT_ORDER_SEMANTICS
    capacity_semantics: str = CAPACITY_SEMANTICS
    initial_residency_semantics: str = INITIAL_RESIDENCY_SEMANTICS
    normalized_trace_digest: str = ""

    def to_json(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["source"] = dict(self.source.__dict__)
        return d


def sha256_file(path: Path) -> Dict[str, Any]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return {"sha256": h.hexdigest(), "byte_size": size}


def canonical_trace_digest(trace: Trace) -> str:
    """A stable digest of everything that matters for deterministic replay:
    layers, per-step routes (post per-token-tuple normalization), and expert
    sizes. Deliberately excludes t_ns (observed wall-clock timing is
    volatile, not part of the deterministic logical contract)."""
    h = hashlib.sha256()
    h.update(json.dumps(sorted(trace.meta.expert_bytes.items()), sort_keys=True).encode())
    for st in trace.steps:
        row = {
            "g": st.graph, "n": st.n_tokens, "ph": st.phase, "seg": st.segment,
            "r": {str(l): [list(tok) for tok in sel] for l, sel in sorted(st.routes.items())},
        }
        h.update(json.dumps(row, sort_keys=True).encode())
    return h.hexdigest()


def _detect_format(path: Path) -> str:
    if path.suffixes[-2:] == [".jsonl", ".gz"] or path.name.endswith(".jsonl.gz"):
        return "argine-router-trace-jsonl-gz"
    if path.suffix == ".jsonl":
        return "argine-router-trace-jsonl"
    raise ContractError(f"unrecognised source trace format: {path.name} "
                        f"(expected .jsonl or .jsonl.gz, Research Part 1's "
                        f"'argine-router-trace' format)")


def normalize(source_path: str | Path, *, source_experiment: Optional[str] = None,
             source_workload: Optional[str] = None, source_split: Optional[str] = None
             ) -> "tuple[Trace, NormalizationReport]":
    """Validate + normalize a source trace into R2Trace v1.

    Fails CLOSED: raises ContractError for anything the contract cannot
    interpret as routing evidence, rather than silently dropping it. The
    only rows ever excluded are rows whose `routes` is empty for every layer
    (a step that selected no expert anywhere -- recorded, not silently
    dropped, and counted in exclusion_reasons)."""
    path = Path(source_path)
    if not path.exists():
        raise ContractError(f"source trace does not exist: {path}")
    source_format = _detect_format(path)
    digest_info = sha256_file(path)

    try:
        trace = Trace.load(path)
    except (TraceError, OSError, gzip.BadGzipFile, json.JSONDecodeError) as e:
        raise ContractError(f"source trace failed to parse as "
                            f"'argine-router-trace': {path}: {e}") from e

    source_row_count = len(trace.steps)

    # ordering: graph numbers must be strictly increasing (section 5:
    # "sequence identity: monotonically ordered canonical sequence number").
    prev_graph = None
    for st in trace.steps:
        if prev_graph is not None and st.graph <= prev_graph:
            raise ContractError(
                f"invalid ordering: step graph={st.graph} does not strictly follow "
                f"the previous step's graph={prev_graph}")
        prev_graph = st.graph

    # expert-id range: fail closed rather than silently accepting an
    # out-of-range id as routing evidence (section 11).
    n_experts = trace.meta.n_experts
    if n_experts:
        for st in trace.steps:
            for l, sel in st.routes.items():
                for tok in sel:
                    for e in tok:
                        if not (0 <= e < n_experts):
                            raise ContractError(
                                f"invalid expert id {e} at step graph={st.graph} "
                                f"layer={l} (n_experts={n_experts})")

    kept_steps: List = []
    excluded = 0
    reasons: Dict[str, int] = {}
    for st in trace.steps:
        if not st.routes or all(not sel for sel in st.routes.values()):
            excluded += 1
            reasons[ExclusionCategory.EMPTY_ROUTES] = reasons.get(
                ExclusionCategory.EMPTY_ROUTES, 0) + 1
            continue
        kept_steps.append(st)

    if not kept_steps:
        raise ContractError(
            f"source trace has zero steps with routing evidence after "
            f"exclusion (source_row_count={source_row_count}, "
            f"excluded={excluded}): not usable for replay")

    normalized = Trace(meta=trace.meta, steps=kept_steps)

    layers_missing_bytes = [l for l in normalized.layers
                            if l not in normalized.meta.expert_bytes]

    has_warmup = any(st.segment == "warmup" for st in normalized.steps)
    initial_residency = "cold_start_documented" if has_warmup else "unknown_cold_assumed"

    n_events = sum(1 for st in normalized.steps for l in normalized.layers if st.experts(l))

    report = NormalizationReport(
        schema_version=SCHEMA_VERSION,
        source=SourceIdentity(
            path=str(path), sha256=digest_info["sha256"], byte_size=digest_info["byte_size"],
            source_format=source_format,
            source_experiment=source_experiment or normalized.meta.source or None,
            source_workload=source_workload or normalized.meta.workload or None,
            source_split=source_split or normalized.meta.split or None,
        ),
        source_row_count=source_row_count,
        normalized_step_count=len(normalized.steps),
        normalized_routing_event_count=n_events,
        excluded_row_count=excluded,
        exclusion_reasons=reasons,
        layers_missing_expert_bytes=layers_missing_bytes,
        initial_residency=initial_residency,
    )
    report.normalized_trace_digest = canonical_trace_digest(normalized)
    return normalized, report


def reconcile(report: NormalizationReport) -> List[str]:
    """Level-C reconciliation checks (section 12). Returns problems; empty
    means reconciliation is clean."""
    problems = []
    if report.source_row_count != report.normalized_step_count + report.excluded_row_count:
        problems.append(
            f"row accounting does not balance: source_row_count="
            f"{report.source_row_count} != normalized_step_count="
            f"{report.normalized_step_count} + excluded_row_count="
            f"{report.excluded_row_count}")
    if report.excluded_row_count and not report.exclusion_reasons:
        problems.append("rows were excluded but no exclusion reason was recorded")
    if sum(report.exclusion_reasons.values()) != report.excluded_row_count:
        problems.append("exclusion_reasons do not sum to excluded_row_count")
    return problems
