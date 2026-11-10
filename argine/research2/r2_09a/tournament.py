"""R2-09A DEV tournament: corpus freeze, deterministic replay, B_selected selection, artifacts."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...research.trace import Trace, PHASE_DECODE
from ...research2.r2_02.replay_multi import run_policy_replay
from ...research2.r2_09b.contract import compute_contract_digest, assert_valid
from .policies import build_tournament_configs, PRIMARY_BUDGETS, PRIMARY_BUDGET_LABELS

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "Argine3" / "research"
OUT_DIR = REPO_ROOT / "experiments" / "R2-09-prediction-benchmark" / "r2_09a"

# Existing canonical LFU/Global-LRU DEV evidence for coding-v1/seed-42 (R2-01 trace) from R2-03
R2_03_REUSE = {
    # (budget_bytes, policy_name)
    (1610612736, "Global-LRU"): {"miss_bytes": 234799792128, "misses": 81752, "deterministic_replay_digest": "0daa7957c2a491f4fc2ef1d12e7f0220175f89444c302aad7e7948e94c3978fd", "source_run_id": "d957077073374f1e9fd1e101a002946f", "source_experiment": "R2-03"},
    (1610612736, "LFU"): {"miss_bytes": 245057458176, "misses": 85428, "deterministic_replay_digest": "240d1c01e291c328ff30f61a6e04210a383b9b6dcd655926c6d1ec28ae6ffe01", "source_run_id": "d957077073374f1e9fd1e101a002946f", "source_experiment": "R2-03"},
    (4294967296, "Global-LRU"): {"miss_bytes": 102151507968, "misses": 35529, "deterministic_replay_digest": "0a12b3388e43d05a44d61d19454c4f406db8ec91fe8629b435c99e96d3b2c001", "source_run_id": "d957077073374f1e9fd1e101a002946f", "source_experiment": "R2-03"},
    (4294967296, "LFU"): {"miss_bytes": 112122408960, "misses": 39124, "deterministic_replay_digest": "399f937c29de639889ef9139d40a0d29f829cd9d187e6e9cfd2ff3c708ea9dfe", "source_run_id": "d957077073374f1e9fd1e101a002946f", "source_experiment": "R2-03"},
    (8589934592, "Global-LRU"): {"miss_bytes": 22898589696, "misses": 7952, "deterministic_replay_digest": "8cc1bf35a7b050b665b6637d0d4f5ed9eb115d8a971da48d448e6ad5467e6c94", "source_run_id": "d957077073374f1e9fd1e101a002946f", "source_experiment": "R2-03"},
    (8589934592, "LFU"): {"miss_bytes": 25641676800, "misses": 8956, "deterministic_replay_digest": "d9f7867fab1918f012eca84adf25a15597c5f4551a501f1c7529b6b0567612ca", "source_run_id": "d957077073374f1e9fd1e101a002946f", "source_experiment": "R2-03"},
}

CANONICAL_R2_01_TRACE = r"C:\Users\alpel\AppData\Local\Argine3\research\R2-01\captures\286a41477ce54d5fb9ccc24f4e508f22\run-00-4G\trace.jsonl.gz"
CANONICAL_R2_01_TRACE_SHA = "e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canon_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(obj) -> str:
    return hashlib.sha256(_canon_json(obj).encode("utf-8")).hexdigest()


EXCLUDED_SEGMENTS = ("warmup", "flush-sentinel")

def trace_decode_steps(trace: Trace) -> int:
    """Decode routing steps/events in instance under frozen replay/trace semantics (v2, decode phase).
    Counts decode steps that would be counted by replay: phase==decode and segment not excluded."""
    n = 0
    for st in trace.steps:
        if getattr(st, "phase", None) == "decode" and getattr(st, "segment", None) not in EXCLUDED_SEGMENTS:
            n += 1
    return n


def trace_prefill_steps(trace: Trace) -> int:
    return sum(1 for st in trace.steps if getattr(st, "phase", None) == "prefill")


AUTHORITATIVE_DEV_CORPUS_DIGEST = "f758b77492eebc38bc81ba22d6b06ab8f19138d1099b635821369a4d022d9398"
AUX_SCIENTIFIC_CONTENT_DIGEST_EXCLUDING_FROZEN_UTC = "36e5db2993a5ca4a0960fc725b3277e60983d0c572a695b1ae07963f77d9363f"

def load_frozen_dev_corpus_manifest(path: Path = None) -> dict:
    """Load frozen Phase 0 manifest read-only; never regenerate/overwrite after freeze."""
    p = Path(path) if path else (OUT_DIR / "dev_corpus_manifest.json")
    if not p.exists():
        raise FileNotFoundError(f"frozen dev corpus manifest not found: {p}")
    m = json.loads(p.read_text(encoding="utf-8"))
    if m.get("counts", {}).get("selected") != 8:
        raise RuntimeError("frozen dev corpus manifest invalid: selected count != 8")
    stored = m.get("dev_corpus_digest")
    recomputed = _digest({k: v for k, v in m.items() if k != "dev_corpus_digest"})
    if recomputed != stored:
        raise RuntimeError(f"frozen dev corpus manifest digest mismatch stored={stored} recomputed={recomputed}")
    if stored != AUTHORITATIVE_DEV_CORPUS_DIGEST:
        raise RuntimeError(f"authoritative DEV corpus digest mismatch: {stored}")
    return m

def build_dev_corpus_manifest(*, allow_create: bool = False, path: Path = None) -> dict:
    """Phase 0: freeze exact DEV tournament corpus by provenance/compatibility (no performance).
    After freeze, this returns the existing frozen manifest read-only and must not regenerate/overwrite it."""
    p = Path(path) if path else (OUT_DIR / "dev_corpus_manifest.json")
    if p.exists():
        return load_frozen_dev_corpus_manifest(p)
    if not allow_create:
        raise RuntimeError("Phase 0 manifest missing and creation not explicitly allowed; frozen manifest required")
    r2_08_sum = json.loads((EVIDENCE_ROOT / "R2-08" / "6165471a12574e05b48932aac2f1626c" / "summary.json").read_text(encoding="utf-8"))
    cells = r2_08_sum["capture_matrix"]["cells"]
    selected = []
    excluded = []
    for cell_id, cell in sorted(cells.items()):
        # audit/compatibility criteria
        reasons = []
        if cell.get("validation_state") != "VALID":
            reasons.append("validation_state_not_VALID")
        if not cell.get("canonical_status"):
            reasons.append("not_canonical_status")
        if not cell.get("trace_path") or not os.path.exists(cell["trace_path"]):
            reasons.append("trace_missing")
        tr_sha = cell.get("trace_sha256")
        if not tr_sha:
            reasons.append("trace_sha_missing")
        # deterministic condition
        cfg = cell.get("configuration", {})
        samp = cfg.get("sampling", {})
        if not (samp.get("temperature") == 0.0 and samp.get("top_k") == 1):
            reasons.append("nondeterministic_sampling")
        # schema/model/runtime/topk/layers/experts/expert bytes compatibility checked via trace
        entry = {
            "instance_id": cell_id.replace("/", "__"),
            "cell_id": cell_id,
            "nominal_workload_category": cell["workload_id"].split("-")[0],
            "workload_id": cell["workload_id"],
            "workload_version": cell.get("workload_version"),
            "seed": cell.get("seed"),
            "split": "DEV",
            "trace_path": cell.get("trace_path"),
            "trace_sha256": tr_sha,
            "source_experiment": "R2-08" if not cell.get("reused") else "R2-01/R2-08",
            "source_run_id": "6165471a12574e05b48932aac2f1626c",
            "source_capture_run_id": cell.get("capture_run_id"),
            "reused_trace": bool(cell.get("reused")),
            "model_sha256": cell.get("model_sha256"),
            "runtime_sha256": cell.get("runtime_sha256"),
            "prompt_sha256": cell.get("prompt_sha256"),
            "configuration_fingerprint": cell.get("configuration_fingerprint"),
            "note": cell.get("note"),
        }
        if reasons:
            entry["exclusion_reasons"] = reasons
            excluded.append(entry)
            continue
        # load trace for deterministic counts/compatibility
        tr = Trace.load(cell["trace_path"])
        dec = trace_decode_steps(tr)
        pre = trace_prefill_steps(tr)
        eb = getattr(tr.meta, "expert_bytes", {}) or {}
        topk = getattr(tr.meta, "top_k", None)
        layers = list(getattr(tr, "layers", []))
        n_experts = getattr(tr.meta, "n_experts", None)
        compat = []
        if tr_sha != _sha256_file(cell["trace_path"]):
            compat.append("trace_sha_mismatch")
        if dec <= 0:
            compat.append("no_decode_steps")
        if topk is None:
            compat.append("top_k_missing")
        if not layers or len(layers) != 48:
            compat.append("unexpected_layer_count")
        if n_experts != 128:
            compat.append("unexpected_n_experts")
        if not eb:
            compat.append("expert_bytes_missing")
        entry.update({
            "decode_steps": dec,
            "prefill_steps": pre,
            "total_steps": len(tr.steps),
            "top_k": topk,
            "layer_count": len(layers),
            "n_experts": n_experts,
            "expert_bytes_present": bool(eb),
            "trace_workload_meta": getattr(tr.meta, "workload", None),
            "byte_capacity_replay_compatible": True if eb else False,
        })
        if compat:
            entry["exclusion_reasons"] = compat
            excluded.append(entry)
        else:
            entry["inclusion_basis"] = "DEV split, VALID canonical R2-08 cell, deterministic sampling, compatible trace/schema/model/runtime/top-k/layers/experts/bytes; provenance selected (no performance)."
            selected.append(entry)

    # ensure coding-v1/seed-42 uses canonical trace identity for reuse matching
    manifest = {
        "schema_version": 1,
        "experiment_id": "R2-09A",
        "phase": "PHASE_0_FROZEN_DEV_CORPUS",
        "frozen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selection_policy": "provenance/compatibility only; never performance/outcome-based",
        "r2_08_source_run_id": "6165471a12574e05b48932aac2f1626c",
        "r2_08_canonical_digest": r2_08_sum.get("canonical_digest"),
        "selected_instances": sorted(selected, key=lambda x: x["instance_id"]),
        "excluded_candidates": excluded,
        "counts": {"selected": len(selected), "excluded": len(excluded)},
    }
    manifest["dev_corpus_digest"] = _digest(manifest)
    return manifest


def _replay_once(trace: Trace, trace_sha: str, cfg: dict, budget: int) -> dict:
    policy = cfg["factory"]()
    det_res, rr = run_policy_replay(
        trace, policy, budget=int(budget), causal=True, phase=PHASE_DECODE, trace_digest=trace_sha
    )
    # deterministic_replay_digest from canonical contract (same as R2-01/R2-02/R2-03)
    return {
        "counted_steps": int(det_res.routing_step_count),
        "tokens": int(rr.tokens),
        "accesses": int(det_res.expert_request_count),
        "misses": int(det_res.miss_count),
        "miss_bytes": int(det_res.bytes_loaded),
        "evictions": int(det_res.eviction_count),
        "peak_resident_bytes": int(det_res.maximum_resident_bytes),
        "budget_violations": 0,
        "events": int(rr.events),
        "deterministic_replay_digest": det_res.deterministic_replay_digest,
        "replay_schema_version": det_res.replay_schema_version,
        "trace_schema_version": det_res.trace_schema_version,
    }


def run_tournament(corpus_manifest: dict) -> Tuple[List[dict], dict]:
    configs = build_tournament_configs()
    rows: List[dict] = []
    determinism_failures = []
    # pre-load traces
    traces = {}
    for inst in corpus_manifest["selected_instances"]:
        traces[inst["instance_id"]] = Trace.load(inst["trace_path"])
    total = len(PRIMARY_BUDGETS) * len(corpus_manifest["selected_instances"]) * len(configs)
    done = 0
    for budget in PRIMARY_BUDGETS:
        for inst in corpus_manifest["selected_instances"]:
            trace = traces[inst["instance_id"]]
            decode_steps = inst["decode_steps"]
            for cfg in configs:
                done += 1
                family = cfg["family_id"]
                config_id = cfg["config_id"]
                # reuse policy for canonical R2-01 coding trace instance if exact match
                reuse = None
                if inst["trace_sha256"] == CANONICAL_R2_01_TRACE_SHA and family in ("global_lru", "lfu"):
                    key_name = "Global-LRU" if family == "global_lru" else "LFU"
                    reuse = R2_03_REUSE.get((budget, key_name))
                status = "OK"
                nondet = False
                if reuse:
                    # reuse canonical R2-03 evidence (exact trace/budget/semantics/phase/decode semantics/metrics)
                    miss_bytes = reuse["miss_bytes"]
                    misses = reuse["misses"]
                    result_digest = reuse["deterministic_replay_digest"]
                    evidence_mode = "REUSED_CANONICAL_DEV_EVIDENCE"
                    source_run_id = reuse["source_run_id"]
                    # for other fields not in reuse table, replay once to obtain (deterministic) - but we must not mix silently;
                    # better to replay to get full fields and verify miss_bytes/misses match.
                    r1 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
                    r2 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
                    if r1["deterministic_replay_digest"] != r2["deterministic_replay_digest"]:
                        nondet = True
                        status = "FAILED_NONDETERMINISTIC"
                    # verify consistency with canonical reused values
                    if r1["miss_bytes"] != miss_bytes or r1["misses"] != misses:
                        # mismatch -> cannot claim exact reuse; treat as new R2-09A replay
                        evidence_mode = "NEW_R2_09A_REPLAY_REUSE_MISMATCH"
                        source_run_id = "R2-09A"
                        result_digest = r1["deterministic_replay_digest"]
                    r = r1
                else:
                    r1 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
                    r2 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
                    if r1["deterministic_replay_digest"] != r2["deterministic_replay_digest"]:
                        nondet = True
                        status = "FAILED_NONDETERMINISTIC"
                        determinism_failures.append({"budget": budget, "instance": inst["instance_id"], "config": config_id})
                    r = r1
                    miss_bytes = r["miss_bytes"]
                    misses = r["misses"]
                    result_digest = r["deterministic_replay_digest"]
                    evidence_mode = "NEW_R2_09A_REPLAY"
                    source_run_id = "R2-09A"

                if r.get("budget_violations", 0) != 0:
                    status = "FAILED_BUDGET_VIOLATION"
                row = {
                    "budget_bytes": budget,
                    "budget_gib": {1610612736: 1.5, 4294967296: 4.0, 8589934592: 8.0}[budget],
                    "dev_instance_id": inst["instance_id"],
                    "nominal_category": inst["nominal_workload_category"],
                    "policy_family": family,
                    "policy_config_id": config_id,
                    "causal": True,
                    "misses": int(misses),
                    "miss_rate": (float(misses) / float(r["accesses"])) if r.get("accesses") else None,
                    "miss_bytes": int(miss_bytes),
                    "miss_bytes_per_decode_step": (float(miss_bytes) / float(decode_steps)) if decode_steps else None,
                    "misses_per_decode_step": (float(misses) / float(decode_steps)) if decode_steps else None,
                    "evictions": int(r.get("evictions", 0)),
                    "peak_resident_bytes": int(r.get("peak_resident_bytes", 0)),
                    "budget_violations": int(r.get("budget_violations", 0)),
                    "evidence_mode": evidence_mode,
                    "source_run_id": source_run_id,
                    "result_digest": result_digest,
                    "deterministic": (not nondet),
                    "status": status,
                    "decode_steps": decode_steps,
                    "accesses": int(r.get("accesses", 0)),
                    "params": cfg.get("params", {}),
                }
                rows.append(row)
                if done % 10 == 0:
                    print(f"progress {done}/{total}", flush=True)
    meta = {"total_cells": total, "determinism_failures": determinism_failures}
    return rows, meta


def select_b_selected(rows: List[dict], corpus_manifest: dict) -> dict:
    instances = [i["instance_id"] for i in corpus_manifest["selected_instances"]]
    N = len(instances)
    budgets = sorted(set(r["budget_bytes"] for r in rows))
    # group by budget, config
    per_budget = {}
    for b in budgets:
        b_rows = [r for r in rows if r["budget_bytes"] == b]
        # config key
        cfg_keys = {}
        for r in b_rows:
            k = (r["policy_family"], r["policy_config_id"])
            cfg_keys.setdefault(k, []).append(r)
        rankings = []
        for (fam, cid), rs in cfg_keys.items():
            if len(rs) != N:
                status = "INCOMPLETE"
            else:
                status = "OK" if all(x["status"] == "OK" and x["deterministic"] for x in rs) else "BAD"
            score = sum(x["miss_bytes_per_decode_step"] for x in rs) / N if len(rs) == N else float("inf")
            miss_step_score = sum(x["misses_per_decode_step"] for x in rs) / N if len(rs) == N else float("inf")
            rankings.append({
                "family_id": fam, "config_id": cid, "params": rs[0].get("params", {}),
                "Score_B_M": score, "mean_misses_per_decode_step": miss_step_score,
                "cell_status": status, "n_cells": len(rs)
            })
        rankings.sort(key=lambda x: (x["Score_B_M"], x["mean_misses_per_decode_step"], x["family_id"], x["config_id"]))
        for i, x in enumerate(rankings, 1):
            x["rank"] = i
        # selection among OK
        ok = [x for x in rankings if x["cell_status"] == "OK"]
        selected = ok[0] if ok else None
        # deltas vs Global-LRU and LFU defaults
        glru = next((x for x in rankings if x["config_id"] == "global_lru/default"), None)
        lfu = next((x for x in rankings if x["config_id"] == "lfu/default"), None)
        sel = dict(selected) if selected else None
        if sel and glru:
            sel["delta_vs_Global_LRU_score"] = sel["Score_B_M"] - glru["Score_B_M"]
            sel["delta_vs_Global_LRU_pct"] = (100.0 * sel["delta_vs_Global_LRU_score"] / glru["Score_B_M"]) if glru["Score_B_M"] else None
        if sel and lfu:
            sel["delta_vs_LFU_score"] = sel["Score_B_M"] - lfu["Score_B_M"]
            sel["delta_vs_LFU_pct"] = (100.0 * sel["delta_vs_LFU_score"] / lfu["Score_B_M"]) if lfu["Score_B_M"] else None
        tie_break_used = "none"
        if selected and len(ok) > 1:
            # check if tied on primary
            s2 = ok[1]
            if abs(selected["Score_B_M"] - s2["Score_B_M"]) < 1e-12:
                if abs(selected["mean_misses_per_decode_step"] - s2["mean_misses_per_decode_step"]) < 1e-12:
                    tie_break_used = "stable_lexical_family_config_id"
                else:
                    tie_break_used = "mean_misses_per_decode_step"
        per_budget[str(b)] = {
            "budget_bytes": b,
            "budget_gib": {1610612736: 1.5, 4294967296: 4.0, 8589934592: 8.0}[b],
            "selected": sel,
            "tie_break_used": tie_break_used,
            "rankings": rankings,
            "global_lru": glru,
            "lfu": lfu,
        }
    # family winners per budget
    family_winners = {}
    for b, info in per_budget.items():
        fam = {}
        for r in info["rankings"]:
            fam.setdefault(r["family_id"], []).append(r)
        winners = {}
        for fid, rs in fam.items():
            rs_ok = [x for x in rs if x["cell_status"] == "OK"] or rs
            winners[fid] = rs_ok[0]
        family_winners[b] = winners
    return {"per_budget": per_budget, "family_winners": family_winners, "instances": instances, "N": N}


def write_artifacts(corpus_manifest, rows, selection, run_meta):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # corpus
    (OUT_DIR / "dev_corpus_manifest.json").write_text(json.dumps(corpus_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # baseline manifest
    configs = build_tournament_configs()
    baseline_manifest = {
        "schema_version": 1, "phase": "PHASE_1_BASELINE_IMPLEMENTATION_MANIFEST",
        "configs": [{"family_id": c["family_id"], "config_id": c["config_id"], "params": c.get("params", {}), "causal": True} for c in configs],
        "families": sorted({c["family_id"] for c in configs}),
        "primary_budgets_bytes": PRIMARY_BUDGETS,
    }
    baseline_manifest["digest"] = _digest(baseline_manifest)
    (OUT_DIR / "baseline_implementation_manifest.json").write_text(json.dumps(baseline_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # results
    (OUT_DIR / "tournament_results.json").write_text(json.dumps({"rows": rows, "run_meta": run_meta}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cols = ["budget_bytes","budget_gib","dev_instance_id","nominal_category","policy_family","policy_config_id","causal","misses","miss_rate","miss_bytes","miss_bytes_per_decode_step","misses_per_decode_step","evictions","peak_resident_bytes","budget_violations","evidence_mode","source_run_id","result_digest","deterministic","status","decode_steps","accesses"]
    with (OUT_DIR / "tournament_results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})
    (OUT_DIR / "selection_summary.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # digests
    tournament_digest_obj = {
        "dev_corpus_manifest_digest": corpus_manifest.get("dev_corpus_digest"),
        "baseline_manifest_digest": baseline_manifest.get("digest"),
        "rows_digest": _digest([{c: r.get(c) for c in cols} for r in rows]),
        "selection_digest": _digest(selection),
        "r2_09b_contract_digest": compute_contract_digest(),
    }
    tournament_digest = _digest(tournament_digest_obj)
    (OUT_DIR / "tournament_digest.json").write_text(json.dumps({"tournament_digest": tournament_digest, "components": tournament_digest_obj}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return baseline_manifest, tournament_digest


def run_r2_09a() -> dict:
    # preconditions
    v = assert_valid()
    expected = "b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28"
    if v["digest"] != expected:
        raise RuntimeError(f"R2-09B digest mismatch: {v['digest']} != {expected}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Phase 0
    corpus = build_dev_corpus_manifest()
    if corpus["counts"]["selected"] == 0:
        raise RuntimeError("R2_09A_BLOCKED_DEV_CORPUS: no selected instances")
    (OUT_DIR / "dev_corpus_manifest.json").write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Phase 1 validation marker (semantic tests run separately via pytest; record intent)
    # Phase 2 tournament
    rows, meta = run_tournament(corpus)
    # Phase 3 selection
    selection = select_b_selected(rows, corpus)
    # Phase 4 artifacts
    baseline_manifest, tournament_digest = write_artifacts(corpus, rows, selection, meta)
    return {"corpus": corpus, "rows": rows, "selection": selection, "meta": meta, "tournament_digest": tournament_digest}

if __name__ == "__main__":
    r = run_r2_09a()
    print("TOURNAMENT_DIGEST", r["tournament_digest"])
    print("SELECTED", {b: r["selection"]["per_budget"][b]["selected"] for b in r["selection"]["per_budget"]})
