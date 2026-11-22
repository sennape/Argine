"""R2-08 figure-ready data (section 42)."""

from __future__ import annotations

from typing import Any, Dict, List


def figure_a_median_adjacent_window_jsd_by_workload(temporal_by_capture: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for key, item in temporal_by_capture.items():
        rows.append({"workload_id": item["workload_id"], "seed": item["seed"],
                     "capture_key": key,
                     "median_adjacent_window_jsd": item.get("median_temporal_jsd")})
    return {"figure": "A", "title": "Median adjacent-window JSD by workload",
            "rows": sorted(rows, key=lambda r: (r["workload_id"], r["seed"]))}


def figure_b_replicate_vs_cross_workload_jsd(replicate: Dict[str, Any],
                                             cross_workload: Dict[str, Any]) -> Dict[str, Any]:
    return {"figure": "B", "title": "Same-workload replicate JSD versus cross-workload JSD",
            "median_same_workload_replicate_jsd": replicate.get("median_replicate_jsd"),
            "median_cross_workload_jsd": cross_workload.get("median_cross_workload_jsd"),
            "replicate_per_workload": replicate.get("per_workload", {}),
            "cross_workload_pairs": cross_workload.get("pairs", [])}


def figure_c_top8_hotset_turnover(temporal_by_capture: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for key, item in temporal_by_capture.items():
        rows.append({"workload_id": item["workload_id"], "seed": item["seed"], "capture_key": key,
                     "median_top8_temporal_jaccard_distance": item.get("median_top8_temporal_jaccard_distance")})
    return {"figure": "C", "title": "Top-8 hot-set turnover across layers/workloads",
            "rows": sorted(rows, key=lambda r: (r["workload_id"], r["seed"]))}


def figure_d_structural_metrics_across_workloads(workload_metrics: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for wid, m in sorted(workload_metrics.items()):
        b = m.get("bridges") or {}
        rows.append({
            "workload_id": wid,
            "r2_05_normalized_entropy": (b.get("r2_05") or {}).get("normalized_entropy_median"),
            "r2_05_effective_expert_fraction": (b.get("r2_05") or {}).get("effective_expert_fraction_median"),
            "r2_05_top8_share": (b.get("r2_05") or {}).get("top8_share_median"),
            "r2_06_nmi": (b.get("r2_06") or {}).get("nmi_median"),
            "r2_06_self_transition_lift": (b.get("r2_06") or {}).get("self_transition_lift_median"),
            "r2_07_immediate_reuse": (b.get("r2_07") or {}).get("immediate_reuse_median"),
            "r2_07_reuse_le4": (b.get("r2_07") or {}).get("reuse_le4_median"),
            "r2_07_reuse_le16": (b.get("r2_07") or {}).get("reuse_le16_median"),
            "r2_07_median_reuse_gap": (b.get("r2_07") or {}).get("median_token_reuse_gap_median"),
            "r2_07_w32_working_set": (b.get("r2_07") or {}).get("w32_working_set_median"),
            "r2_07_w128_working_set": (b.get("r2_07") or {}).get("w128_working_set_median"),
        })
    return {"figure": "D", "title": "R2-05 / R2-06 / R2-07 structural metrics across workload families",
            "rows": rows}


def build_figure_data(temporal_by_capture: Dict[str, Any], replicate: Dict[str, Any],
                      cross_workload: Dict[str, Any],
                      workload_metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "figure_a": figure_a_median_adjacent_window_jsd_by_workload(temporal_by_capture),
        "figure_b": figure_b_replicate_vs_cross_workload_jsd(replicate, cross_workload),
        "figure_c": figure_c_top8_hotset_turnover(temporal_by_capture),
        "figure_d": figure_d_structural_metrics_across_workloads(workload_metrics),
        "optional_pairwise_workload_jsd_heatmap_data": cross_workload.get("pairs", []),
    }
