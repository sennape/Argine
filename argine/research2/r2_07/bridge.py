"""R2-07 sections 16/17/33: the R2-06 bridge (one-step -> multi-step
locality), the R2-05 bridge (frequency vs reuse-soon distinction, with
an optional descriptive Spearman correlation), and loading both
upstream experiments' canonical evidence for the section-33 digest
binding.

R2-06 BRIDGE (section 16): R2-06 found measurable one-step conditional
routing structure (median NMI, median self-transition lift). The
correct, non-overreaching interpretation this module states is exactly
the task's own formulation: "R2-06 identified immediate conditional
structure; R2-07 measures the temporal horizon and working-set
consequences of that structure." R2-07 NEVER claims R2-06 already
proved long-range locality -- that is precisely what R2-07 itself
tests, for the first time, here.

R2-05 BRIDGE (section 17): R2-05 showed frequency concentration. An
expert being FREQUENT is a logically distinct property from an expert
being REUSED SOON -- a high-frequency expert may still have long reuse
gaps. This module computes a SECONDARY, descriptive Spearman rank
correlation between (a) each layer-local expert's R2-05 request
frequency and (b) that same layer-local expert's own median token reuse
gap (computed here, from reuse events with at least one reuse). This is
explicitly secondary, non-causal, and never a predictor claim (section
17's own instruction).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import store


def load_r2_05_canonical_evidence(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Dynamically loads the latest R2-05 PASS run's frequency evidence
    (never a hard-coded path/run id) -- the same self-contained pattern
    r2_06.bridge uses (duplicated here rather than imported, so R2-07
    stays independent of r2_06's internals apart from its own explicit,
    separate bridge module)."""
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
    r2_05_digest = None
    summary_path = Path(entry["run_dir"]) / "summary.json"
    if summary_path.is_file():
        import json
        try:
            r2_05_digest = json.loads(summary_path.read_text(encoding="utf-8")).get(
                "canonical_digest")
        except (ValueError, OSError):
            r2_05_digest = None
    return {"r2_05_run_id": entry["identity"]["run_id"], "frequency_rows": rows,
           "r2_05_canonical_digest": r2_05_digest}


def load_r2_06_canonical_evidence(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Dynamically loads the latest R2-06 PASS run's summary.json
    (run_id, canonical_digest, headline) -- needed both for the section-
    16 interpretive bridge text and for binding R2-06's run id/digest
    into R2-07's own canonical digest (section 33)."""
    registry = [e for e in store.load_registry(root)
               if e.get("identity", {}).get("experiment_id") == "R2-06"
               and e.get("identity", {}).get("outcome") == "PASS"]
    if not registry:
        return None
    latest_ends = sorted({e["identity"]["end_utc"] for e in registry}, reverse=True)
    newest = [e for e in registry if e["identity"]["end_utc"] == latest_ends[0]]
    entry = sorted(newest, key=lambda e: e["identity"]["run_id"])[0]
    summary_path = Path(entry["run_dir"]) / "summary.json"
    if not summary_path.is_file():
        return None
    import json
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return {
        "r2_06_run_id": entry["identity"]["run_id"],
        "r2_06_canonical_digest": summary.get("canonical_digest"),
        "r2_06_headline": summary.get("headline", {}),
    }


R206_BRIDGE_NOTE = (
    "R2-06 identified immediate (one-step) conditional routing structure "
    "(measurable normalized mutual information and self-transition lift "
    "between consecutive same-layer routing decisions). R2-07 measures the "
    "temporal horizon and working-set consequences of that structure -- how "
    "far it persists, and how the active expert set grows as the "
    "observation window expands. R2-06's one-step finding does not, by "
    "itself, establish long-range locality; that is exactly what R2-07 "
    "tests."
)

R205_BRIDGE_NOTE = (
    "R2-05 showed frequency concentration (some experts carry a "
    "disproportionate share of requests). Being FREQUENT is a logically "
    "distinct property from being REUSED SOON: a high-frequency expert may "
    "still have a long median reuse gap if its occurrences are spread "
    "evenly across the whole trace rather than clustered. R2-07 computes a "
    "secondary, descriptive Spearman rank correlation between per-expert "
    "request frequency (R2-05) and median token reuse gap (R2-07) to "
    "characterize, not causally explain, this distinction."
)


def _rank(values: Sequence[float]) -> List[float]:
    """Average ("fractional") rank, 1-indexed, with ties receiving the
    mean of the ranks they would otherwise occupy -- the standard
    convention for Spearman rank correlation."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_correlation(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation (Pearson correlation of the ranks),
    implemented locally (no scipy dependency) -- a clearly-defined,
    standard descriptive measure of monotonic association (section 17's
    own preference). None (fail-closed) for fewer than 2 paired
    observations, or when either rank series has zero variance (a
    well-defined correlation does not exist in that degenerate case)."""
    n = len(x)
    if n != len(y) or n < 2:
        return None
    rx = _rank(x)
    ry = _rank(y)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    var_x = sum((a - mean_rx) ** 2 for a in rx)
    var_y = sum((b - mean_ry) ** 2 for b in ry)
    if var_x == 0.0 or var_y == 0.0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def frequency_vs_reuse_gap_correlation(frequency_rows: List[Dict[str, Any]],
                                       layer_reuse_events: Dict[int, List[Dict[str, Any]]]
                                       ) -> Dict[str, Any]:
    """Builds the paired (frequency, median_token_reuse_gap) sample across
    every layer-local expert that has (a) an R2-05 frequency row and (b)
    at least one R2-07 reuse event (an expert with zero reuse events has
    no median gap and is excluded from the sample, not assigned a
    fabricated value), then computes the Spearman correlation."""
    from . import reuse_stats
    gaps_by_expert: Dict[Tuple[int, int], List[int]] = {}
    for layer, events in layer_reuse_events.items():
        for ev in events:
            gaps_by_expert.setdefault((layer, ev["expert_id"]), []).append(ev["token_reuse_gap"])

    freq_by_expert = {(row["layer_id"], row["expert_id"]): row["request_count"]
                      for row in frequency_rows}

    paired_frequency: List[float] = []
    paired_median_gap: List[float] = []
    sample: List[Dict[str, Any]] = []
    for key, gaps in sorted(gaps_by_expert.items()):
        if key not in freq_by_expert:
            continue
        median_gap = reuse_stats.quantile(sorted(gaps), 0.50)
        paired_frequency.append(freq_by_expert[key])
        paired_median_gap.append(median_gap)
        sample.append({"layer_id": key[0], "expert_id": key[1],
                       "request_frequency": freq_by_expert[key],
                       "median_token_reuse_gap": median_gap})

    correlation = spearman_correlation(paired_frequency, paired_median_gap)
    return {
        "n_paired_observations": len(sample), "spearman_correlation": correlation,
        "sample": sample,
        "note": "descriptive monotonic-association measure only; no causal "
               "interpretation, no predictor claim (section 17).",
    }
