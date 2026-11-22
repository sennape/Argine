"""R2-06 section 13 (part): the R2-05 bridge -- reconciling R2-06's own
transition-derived target marginal against R2-05's canonical per-layer
frequency marginal.

The two marginals are NOT expected to match exactly, and this module
never requires exact equality (section 13 is explicit about this):

  - R2-05's marginal is built from the single-expert REQUEST unit
    (one occurrence of one expert id within one token's selected
    tuple), over every token the trace records for that layer.
  - R2-06's transition-derived marginal is built from the TRANSITION
    unit (pairwise source/target expansion over CONSECUTIVE token
    pairs), which structurally drops the very first and very last
    token's selection for that layer (a token with no predecessor
    contributes no observation as a target; a token with no successor
    contributes no observation as a source) and multiplies every
    interior token's contribution by surrounding selection sizes.

Both are legitimate marginals of DIFFERENT underlying unit definitions;
this module reports the per-expert divergence between them and flags
only a GROSS, structural disagreement (one expert dominant in one
marginal but entirely absent from the other, or a divergence far beyond
what edge-token truncation alone could explain) as a compatibility
concern -- never treating ordinary small, edge-effect-explained
divergence as a problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store

# A per-expert absolute-probability-difference beyond this, for an expert
# present in both marginals, is flagged as worth a human look -- NOT a
# hard FAIL condition (R2-06's own outcome classification never fails
# solely because of this bridge; see orchestrator.classify_outcome).
# Predeclared, not tuned to any real R2-06 result.
GROSS_DIVERGENCE_THRESHOLD = 0.25


def load_r2_05_canonical_evidence(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Dynamically loads the latest R2-05 PASS run's frequency evidence
    (never a hard-coded path/run id): its expert_frequency.csv, parsed
    back into row dicts with layer_id/expert_id/request_fraction_within_layer
    (the exact fields R2-05's own frequency.layer_frequency_rows produced).
    Returns None if no R2-05 PASS run or its evidence file is found -- the
    caller treats that as a bridge-unavailable, non-fatal condition (R2-05
    PASS is already a hard prerequisite enforced by prerequisite.py before
    R2-06 runs at all, so this should not normally be None in practice; it
    is handled defensively regardless)."""
    registry = [e for e in store.load_registry(root)
               if e.get("identity", {}).get("experiment_id") == "R2-05"
               and e.get("identity", {}).get("outcome") == "PASS"]
    if not registry:
        return None
    latest_ends = sorted({e["identity"]["end_utc"] for e in registry}, reverse=True)
    newest = [e for e in registry if e["identity"]["end_utc"] == latest_ends[0]]
    entry = sorted(newest, key=lambda e: e["identity"]["run_id"])[0]
    frequency_csv_path = Path(entry["run_dir"]) / "expert_frequency.csv"
    if not frequency_csv_path.is_file():
        return None
    import csv
    try:
        with frequency_csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = [{
                "layer_id": int(row["layer_id"]), "expert_id": int(row["expert_id"]),
                "request_count": int(row["request_count"]),
                "request_fraction_within_layer": float(row["request_fraction_within_layer"]),
            } for row in reader]
    except (OSError, ValueError, KeyError):
        return None
    return {"r2_05_run_id": entry["identity"]["run_id"], "frequency_rows": rows}


def reconcile_layer_marginal(layer: int, r2_06_target_marginal: Dict[int, float],
                             r2_05_frequency_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-expert comparison between R2-06's transition-derived target
    marginal and R2-05's request-unit marginal, for ONE layer.
    `r2_05_frequency_rows` are R2-05's own frequency_rows for this layer
    (layer_id, expert_id, request_fraction_within_layer, ...), loaded by
    the orchestrator from R2-05's evidence."""
    r2_05_marginal = {row["expert_id"]: row["request_fraction_within_layer"]
                     for row in r2_05_frequency_rows if row["layer_id"] == layer}
    all_experts = sorted(set(r2_06_target_marginal) | set(r2_05_marginal))
    per_expert = []
    max_abs_diff = 0.0
    flagged: List[int] = []
    for expert_id in all_experts:
        p_06 = r2_06_target_marginal.get(expert_id, 0.0)
        p_05 = r2_05_marginal.get(expert_id, 0.0)
        diff = abs(p_06 - p_05)
        max_abs_diff = max(max_abs_diff, diff)
        if diff > GROSS_DIVERGENCE_THRESHOLD:
            flagged.append(expert_id)
        per_expert.append({"expert_id": expert_id, "r2_06_transition_marginal": p_06,
                           "r2_05_request_marginal": p_05, "absolute_difference": diff})
    return {
        "layer_id": layer, "per_expert": per_expert, "max_absolute_difference": max_abs_diff,
        "gross_divergence_threshold": GROSS_DIVERGENCE_THRESHOLD,
        "flagged_experts": flagged,
        "compatible": not flagged,
    }


def reconcile_all_layers(target_marginals: Dict[int, Dict[int, float]],
                         r2_05_frequency_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    per_layer = {layer: reconcile_layer_marginal(layer, target_marginals[layer],
                                                 r2_05_frequency_rows)
                for layer in sorted(target_marginals)}
    all_compatible = all(v["compatible"] for v in per_layer.values())
    return {
        "per_layer": per_layer, "all_layers_compatible": all_compatible,
        "note": "Marginals are built from DIFFERENT unit definitions (R2-05: single-"
               "expert request unit; R2-06: transition-derived, pairwise-expanded "
               "target unit) and are not expected to match exactly; only a GROSS "
               "divergence (> gross_divergence_threshold for some expert) is flagged.",
    }
