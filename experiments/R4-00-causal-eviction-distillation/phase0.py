"""Research 4 Phase 0 -- Causal Future-Distance Distillation (CFDC): contract builder and verifier.

Phase 0 only: this module writes and verifies the frozen protocol. It contains NO replay, NO training and NO scoring code,
never opens an EVAL or VALIDATION trace, and creates no outcome metric.

    python phase0.py build    # (re)write the JSON contract and FREEZE.json from the definitions below
    python phase0.py verify   # re-check every frozen identity, the freeze digest, EVAL lock and Research 3 immutability
"""
from __future__ import annotations

import argparse, hashlib, json, subprocess, sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EXP = REPO / "experiments"

STATE = "R4_CFDC_PHASE0_FROZEN"
EVAL_STATE = "LOCKED"
BRANCH = "r4-main"
SOURCE_R3_HEAD = "f88001558542b648c0a3abff54c919adedf27809"   # r3-main HEAD when r4-main was created
R3_CLOSURE_COMMIT = "3aa389eb31da6962711d74413b992803ae20b544"
R3_PAPER_COMMIT = "337000808896e54955de0278686c5b7dae001b4e"
R3_CLOSURE_FREEZE = "760a22d69abdd207c7ecd4b4f1012b85711b20aa3cb623cddb55d0c8802a5717"
R3_CLASSIFICATION = "R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME"
BUDGET_BYTES = 1610612736  # 1.5 GiB, the Research 3 primary budget

CONTRACT_JSON = ["PROTOCOL.json", "DATA_SPLITS.json", "FEATURE_CONTRACT.json", "BASELINES.json", "MODEL_CONTRACT.json", "DECISION_RULES.json"]
CONTRACT_MD = ["README.md", "PREREGISTRATION.md", "LITERATURE_BOUNDARY.md", "CLAIM_BOUNDARIES.md"]
# Frozen code the later phases must bind to (LF-normalized sha256 of the content at SOURCE_R3_HEAD).
BOUND_CODE = [
    "argine/research/oracle.py", "argine/research/replay.py", "argine/research/trace.py", "argine/research/policies.py",
    "argine/research2/r2_09a/policies.py", "argine/research2/r2_bridge01/retention_policies.py", "argine/research2/r2_10/predictors.py",
    "experiments/R3-05-economic-retention/r4-nue-candidate-gate/r4.py", "experiments/R3-05-economic-retention/phase0.py",
]
# Phase 0 must never create any of these.
FORBIDDEN_OUTCOME_NAMES = ["RESULTS", "EVAL_RESULT", "VALIDATION_RESULT", "TRAIN_RESULT", "CAPTURE", "SELECTION", "LABELS", "MODEL_FIT", "CLASSIFICATION.json"]


class Phase0Error(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def sha_file(p: Path) -> str: return sha_bytes(p.read_bytes())
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
def jl(rel: str) -> Any: return json.loads((REPO / rel).read_text(encoding="utf-8"))
def lf_sha(b: bytes) -> str:
    """SHA-256 of content with CRLF normalized to LF (the Windows checkout uses CRLF, git stores LF)."""
    return sha_bytes(b.replace(b"\r\n", b"\n"))


def committed_lf_sha(rel: str, rev: str = SOURCE_R3_HEAD) -> str:
    return lf_sha(subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{rel}"], capture_output=True, check=True).stdout)


# ====================================================================== data splits
DEV_MANIFEST = "experiments/R2-09-prediction-benchmark/r2_09a/dev_corpus_manifest.json"
EVAL_SEALED = "experiments/R2-10-working-set-predictor/PHASE3B-EVAL-CAPTURE-SEALED.json"
R2_11_SEALED = "experiments/R2-11-predictive-retention/R2_11_CAPTURED_SEALED.json"
R2_11_CELLS = "experiments/R2-11-predictive-retention/eval_cells"


def data_splits() -> Dict[str, Any]:
    dev = jl(DEV_MANIFEST)
    def dev_rows(seed: int) -> List[Dict[str, Any]]:
        return [{"instance_id": i["instance_id"], "workload_id": i["workload_id"], "nominal_category": i["nominal_workload_category"], "seed": i["seed"],
                 "trace_path_host": i["trace_path"], "trace_sha256": i["trace_sha256"], "prompt_sha256": i["prompt_sha256"], "decode_steps": i["decode_steps"],
                 "total_steps": i["total_steps"], "frozen_A_reference": "experiments/R3-05-economic-retention/r4-nue-candidate-gate/DEV_RESULTS.json runs[instance].A"}
                for i in dev["selected_instances"] if i["seed"] == seed]
    r211 = jl(R2_11_SEALED)
    val = []
    for c in r211["canonical_captures"]:
        w = c["workload_id"]; cell = jl(f"{R2_11_CELLS}/{w}__budget-1.5__b_selected.json")
        if cell["input_trace_sha256"] != c["trace_sha256_independently_recomputed"]:
            raise Phase0Error(f"R2-11 cell/trace hash mismatch for {w}")
        val.append({"workload_id": w, "nominal_category": c["category"], "workload_spec_path": c["workload_spec_path"], "workload_spec_sha256": c["workload_spec_sha256_frozen"],
                    "trace_path_host": f"%LOCALAPPDATA%\\Argine3\\research\\R2-11\\captures\\{w}\\run-00-4G\\trace.jsonl.gz",
                    "trace_sha256": c["trace_sha256_independently_recomputed"], "normalized_trace_digest": c["normalized_trace_digest"],
                    "frozen_A_reference": {"source": f"{R2_11_CELLS}/{w}__budget-1.5__b_selected.json", "decode_bytes_missed": cell["result"]["bytes_missed"],
                                           "decode_misses": cell["result"]["misses"], "decode_steps": cell["result"]["steps"]},
                    "prior_exposure": "opened in R2-11 for b_selected and predicted_protect miss bytes only" + (
                        "; same prompt as the Research 3 canonical stream (r2-11-coding-v1), on which NUE_X was scored in Research 3" if w == "r2-11-coding-v1" else "")})
    ev = jl(EVAL_SEALED)
    eva = [{"workload_id": c["workload_id"], "nominal_category": c["nominal_workload_category"], "workload_spec_path": c["frozen_workload_source_path"],
            "workload_spec_sha256": c["frozen_workload_sha256"], "trace_path_host": c["trace_path_host"], "trace_sha256": c["trace_sha256_recomputed"],
            "normalized_step_count": c.get("normalized_step_count"),
            "prior_exposure": "opened in R2-10 Phase 3 for predictor recall@k only; no eviction or retention outcome has ever been computed on it"} for c in ev["cells"]]
    return {
        "artifact": "R4-00-DATA-SPLITS",
        "replay_budget_bytes": BUDGET_BYTES,
        "trace_capture_note": "All traces were captured at 4 GiB; only routing is used. Replay at 1.5 GiB is valid because replay depends on routing, not on the capture budget (Research 2 replay validation).",
        "splits": {
            "TRAIN": {"role": "fit CHEAP and RICH; teacher labels", "source_manifest": DEV_MANIFEST, "source_manifest_sha256": sha_file(REPO / DEV_MANIFEST), "traces": dev_rows(42)},
            "VALIDATION": {"role": "the single frozen gate (no selection among alternatives)", "source_record": R2_11_SEALED, "source_record_sha256": sha_file(REPO / R2_11_SEALED),
                           "traces": val, "freshness_reference": "experiments/R2-11-predictive-retention/freshness_audit.json (max cross-corpus 8-gram Jaccard 0.0367, R2-11 vs R2-09 EVAL)"},
            "EVAL": {"role": "scored exactly once after TRAIN fit and VALIDATION scoring are locked", "source_record": EVAL_SEALED, "source_record_sha256": sha_file(REPO / EVAL_SEALED),
                     "traces": eva, "state": EVAL_STATE},
            "REPLICATE_CHECK": {"role": "non-gating descriptive replicate of TRAIN; never used for fitting or any decision", "traces": dev_rows(43)},
        },
        "mapping_from_request": {
            "TRAIN": "as requested: frozen DEV seed-42 corpus of the prior predictor work (R2-09A).",
            "VALIDATION": "DEVIATION, decided by the investigator on 2026-11-22: the requested 'independent DEV seed-43 corpus' is not independent. Under greedy decoding "
                          "seed-43 routing is identical to seed-42 on every step except the last (coding 394/395 aligned steps, other workloads 523/524). The R2-11 "
                          "confirmatory corpus (12 prompt-disjoint traces) replaces it; seed-43 is kept as REPLICATE_CHECK.",
            "EVAL": "the canonical held-out R2-09 EVAL corpus (4 workloads) used prospectively by the closed causal-predictor work (R2-10).",
        },
        "seed43_independence_audit": {
            "method": "Trace.load of both DEV traces; per step compare n_tokens and the sorted expert set of each of 48 layers (Phase 0, DEV data only)",
            "result": {"coding-v1": "steps 396 vs 395; 394 identical; only step 394 differs (4 layers)",
                       "instruction-v1": "524 vs 524; 523 identical", "reasoning-v1": "524 vs 524; 523 identical; only step 523 differs",
                       "summarization-v1": "524 vs 524; 523 identical"},
            "conclusion": "seed-43 is a near-duplicate of seed-42, not an independent validation set",
        },
        "no_overlap_rules": ["trace_sha256 sets of TRAIN, VALIDATION, EVAL, REPLICATE_CHECK are pairwise disjoint",
                             "workload ids of TRAIN/REPLICATE_CHECK (DEV *-v1), VALIDATION (r2-11-*) and EVAL (*-eval-v1) are pairwise disjoint",
                             "VALIDATION and EVAL workload spec sha256 sets are disjoint"],
        "access_rule": "Phase 0 reads no VALIDATION or EVAL trace file. Identities are taken from the sealed records listed above.",
    }


# ====================================================================== features
CLOCKS = {
    "j": "global served-event index: 0-based position in the list of (step, layer, experts) events served by the frozen replay loop (all served steps, prefill and decode, layer order 0..47 within a step; layers with no request are not events)",
    "i": "served step index (trace step number) of the current event",
    "n_l": "per-layer event counter: number of served events of layer l strictly before the current moment",
}
FEATURES = [
    ("F01", "events_since_last_ref", "j_now - j_last_ref(k)", "semantic events", "on every request of k (hit or admission)", "n/a: a resident key has been requested at least at admission", "integer; ranking ties per model rule", "past requests only"),
    ("F02", "decayed_count_fx", "floor(2^16 * S_k(i_now)), S_k(i) = sum over past requests r of k of 0.99^(i - i_r); request at the current event excluded", "fixed point 2^-16 requests", "S updated lazily on request: S <- S*0.99^(i - i_last) + 1; evaluated at decision", "0", "integer", "past requests only; decay 0.99 per served step (the frozen decayed_transition decay)"),
    ("F03", "events_since_admission", "j_now - j_admit(k) for the current residency", "semantic events", "set at admission", "n/a", "integer", "own admission time"),
    ("F04", "hits_since_admission", "number of requests of k served as hits since its current admission", "requests", "incremented on hit", "0 at admission", "integer", "past hits only"),
    ("F05", "reload_count", "number of admissions of k before the current one, since replay start", "admissions", "incremented at admission", "0 at first admission", "integer", "past admissions only"),
    ("F06", "prev_reuse_interval", "n_l at the last request of k minus n_l at the request before it", "layer events", "on request", "sentinel 1000000 if k has fewer than 2 past requests", "integer", "past requests only"),
    ("F07", "mean_reuse_interval", "floor(sum of all past inter-request intervals of k / their count)", "layer events", "on request", "sentinel 1000000 if fewer than 2 past requests", "integer (floor)", "past requests only"),
    ("F08", "in_prev_layer_event", "1 if expert e was requested by layer l's most recent served event strictly before the current event, else 0", "boolean", "after each served event of layer l", "0 if layer l has no previous event", "integer 0/1", "the last completed event of the candidate's own layer"),
    ("F09", "occupancy_permille", "floor(1000 * resident_bytes / budget_bytes) immediately before this eviction", "permille", "per decision", "n/a", "integer; identical for all candidates of a decision", "current cache state"),
    ("F10", "pending_misses", "number of missed experts of the current event not yet admitted, including the one being admitted", "experts", "per decision", "n/a", "integer; identical for all candidates of a decision", "current request (known at decision time)"),
    ("F11", "recency_rank", "rank of k among the decision's candidates by j_last_ref descending (0 = most recent); ties by (layer, expert) ascending", "rank", "per decision", "n/a", "integer, unique within a decision", "past requests only"),
    ("F12", "frequency_rank", "rank of k among the decision's candidates by F02 descending (0 = highest); ties by F01 ascending, then (layer, expert) ascending", "rank", "per decision", "n/a", "integer, unique within a decision", "past requests only"),
    ("F13", "expert_bytes", "byte size of k as given by the replay context (ctx.bytes_of(layer))", "bytes", "static", "n/a", "integer", "model file metadata"),
    ("F14", "layer_id", "l", "index 0..47", "static", "n/a", "integer", "static identity"),
    ("F15", "expert_id", "e", "index 0..127", "static", "n/a", "integer", "static identity"),
    ("F16", "layer_cycle_offset", "((l - L_now - 1) mod 48) + 1 where L_now is the current event's layer", "layers", "per decision", "n/a", "integer 1..48", "fixed layer execution order of the architecture; uses no routing of later events"),
]


def feature_contract() -> Dict[str, Any]:
    return {
        "artifact": "R4-00-FEATURE-CONTRACT",
        "vocabulary_size": len(FEATURES),
        "clocks": CLOCKS,
        "candidate_set": "residents not requested by the current event (current-event shield); if empty, all residents (total fallback). Features are computed only for candidates.",
        "scope": "one replay per trace, cold cache at trace start; every clock, feature history and label spans all served events of that trace (prefill and decode); only decode-event misses are scored; nothing carries over between traces.",
        "exposure_attestation": "The vocabulary was written on 2026-11-22 from the request text, Research 3 mechanisms (current-event shield, last-observed set) and the cited literature. No VALIDATION or EVAL statistic was read in Phase 0. Prior EVAL exposure is limited to R2-10 predictor recall@k (DATA_SPLITS).",
        "state_snapshot": "after all previous events and after the current event's requests processed so far (experts processed in ascending expert id; hits update state before misses are admitted in that order); the request being admitted is known.",
        "features": [{"id": f, "name": n, "definition": d, "units": u, "update_timing": t, "initial_value": iv, "tie_semantics": ts, "causal_justification": cj,
                      "depends_on_future": False, "cheap_eligible": True, "integer_valued": True} for f, n, d, u, t, iv, ts, cj in FEATURES],
        "forbidden_inputs": ["next_uses / any next-use index", "any request of a future event", "teacher target or Belady state", "EVAL or VALIDATION statistics of any kind",
                             "NUE_X / decayed_transition predicted sets (kept as a comparator, not a feature, so CHEAP stays predictor-free)"],
        "freeze_rule": "No feature may be added, removed or redefined after Phase 0.",
    }


# ====================================================================== baselines
def baselines() -> Dict[str, Any]:
    hc_sha = sha_file(REPO / "experiments/R3-05-economic-retention/r4-nue-candidate-gate/HEADROOM_CAPTURE.json")
    return {
        "artifact": "R4-00-BASELINES",
        "replay": {"loop": "experiments/R3-05-economic-retention/r4-nue-candidate-gate/r4.py:dev_replay (mirrors the frozen R2-09A fast_replay accounting: every served step replayed, decode steps counted, served_steps(trace, PHASE_DECODE, 'v2'))",
                   "budget_bytes": BUDGET_BYTES, "admission": "demand admission, event-atomic multi-key events",
                   "byte_accounting": "decode miss bytes = sum over counted (decode) events of len(missed) * ctx.bytes_of(layer); identical to Research 3"},
        "reference": {"id": "A_B_SELECTED", "definition": "b_selected = AgedFrequency, decay 0.999 per access, aging interval 1 (r4.py make_arm('A'))",
                      "frozen_values": "TRAIN: R3-05 R4 DEV_RESULTS A rows; VALIDATION: R2-11 eval_cells b_selected 1.5 GiB (see DATA_SPLITS); EVAL: none exists (computed once in the EVAL phase)",
                      "retrained_or_modified": False},
        "oracle": {"id": "BELADY", "definition": "argine.research.oracle.BeladyGlobal on the same served event list: evict the non-member resident with the farthest next use (never-used-again first); demand admission; not proven optimal for multi-key, two-size events",
                   "role": "denominator of the primary endpoint; clairvoyant, never a candidate"},
        "comparators": [
            {"id": "NUE_X", "definition": "Research 3 NUE_X, lexicographic key LEX, as frozen: r4.py make_nue('X', 'LEX'), decayed_transition decay 0.99, top_k 8, with current-event shield",
             "retrained_or_modified": False,
             "historical_value_not_reused": {"capture_of_belady_byte_headroom": "8.79% (Research 3, canonical r2-11-coding-v1 stream, 1.5 GiB)",
                                             "source": "experiments/R3-05-economic-retention/r4-nue-candidate-gate/HEADROOM_CAPTURE.json", "source_sha256": hc_sha,
                                             "note": "historical Research 3 value on a different stream; Research 4 re-scores NUE_X under the same replay on its own splits. The Research 3 result is unchanged."}},
            {"id": "DECAYED_COUNT", "definition": "evict the candidate with the LOWEST S_k (F02 exact, IEEE-754 binary64 evaluated as S*0.99^(i - i_last)+1 per request, decay 0.99 per served step, no sweep); ties: larger F01 (older), then layer ascending, then expert ascending; current-event shield with total fallback",
             "retrained_or_modified": "new in Research 4; definition frozen here, no hyperparameter search"},
        ],
        "distinction": "Historical frozen values are cited only as provenance. Every capture used by a Research 4 decision is a new Research 4 replay value on the frozen splits.",
    }


# ====================================================================== models
def model_contract() -> Dict[str, Any]:
    vocab = [f[1] for f in FEATURES]
    return {
        "artifact": "R4-00-MODEL-CONTRACT",
        "teacher": {
            "trajectory": "TRAIN labels come from the A (b_selected) replay state trajectory on each TRAIN trace (the frozen reference policy); the learned policies are evaluated under their own cache state.",
            "decision_unit": "each eviction required while admitting one missed expert; several evictions of one event are separate decisions in sequence",
            "distance": "d = nu_j(k) - j, nu from argine.research.oracle.next_uses over the served event list; never used again => inf",
            "target": "y = log2(1 + d) for finite d; y = 21.0 for d = inf. Phase 1 asserts that every trace has fewer than 2^20 served events, so every finite y < 20 < 21.",
            "teacher_victim": "argmax d over candidates (Belady rule); used only for diagnostics",
            "sampling": "eviction decisions of the A trajectory are numbered 0, 1, 2, ... per TRAIN trace over all served events (prefill included); decisions with index mod 64 == 0 are sampled; all candidates of a sampled decision are rows; total decisions and rows are reported",
            "shift_diagnostic": "non-gating: on TRAIN, report agreement of CHEAP's victim with the teacher victim under A's states and under CHEAP's own states",
            "no_future_in_policy": "targets never enter features; the deployed policies see only FEATURE_CONTRACT inputs",
        },
        "feature_order": vocab,
        "CHEAP": {
            "class": "single CART regression tree", "loss": "squared error on y", "max_depth": 3, "max_distinct_features": 4, "min_samples_leaf": 1000,
            "min_decisions_per_leaf": 50, "leaf_support_rule": "a split is admissible only if each child holds >= 1000 rows drawn from >= 50 distinct sampled decisions",
            "split_search": "exhaustive over integer thresholds t (left branch: x <= t); maximize SSE reduction; ties: lower feature index in feature_order, then lower t",
            "stop": "a node is a leaf if depth = 3, if no split leaves >= min_samples_leaf rows on both sides, or if the best SSE reduction is <= 0",
            "four_feature_procedure": "fit on all 16 features; if more than 4 distinct features are used, rank features by total SSE reduction in that tree (ties: lower index), keep the top 4, refit on those 4 with the same settings. TRAIN only.",
            "export": "nested integer comparisons; each leaf stores an integer leaf rank = position of its mean y in ascending order over leaves (ties by node id); no floating point at decision time",
            "eviction_rule": "evict the candidate with the highest leaf rank; ties: larger F01 (LRU), then layer ascending, then expert ascending",
            "forbidden": ["ensembles", "neural networks", "online learning", "a second CHEAP architecture"],
            "deterministic": True, "random_seed": 0,
        },
        "RICH": {
            "class": "gradient-boosted regression trees (single configuration, numpy implementation)", "loss": "squared error on y", "rounds": 200, "learning_rate": 0.1,
            "max_depth": 6, "min_samples_leaf": 200, "binning": "per feature, edges = distinct values if <= 255, else the 255 quantile cut points of TRAIN rows (numpy.quantile, method='lower'); bins fixed from TRAIN",
            "init": "mean of y on TRAIN", "subsampling": "none (rows and features)", "split_ties": "lower feature index, then lower bin",
            "eviction_rule": "predictions are quantized to integers round(pred * 2^16) before ranking; evict the maximum; ties: larger F01, then layer ascending, then expert ascending",
            "determinism": "pure numpy, single thread; numpy version and SHA-256 of the fitted model recorded in the Phase 1 lock",
            "role": "diagnostic information ceiling of the frozen feature vocabulary; NOT a runtime candidate; inference cost ignored",
            "deterministic": True, "random_seed": 0,
        },
        "shared_policy_rules": {"candidate_set": "current-event shield with total fallback (FEATURE_CONTRACT)", "admission": "demand admission",
                                "implementation_dependencies": "numpy only (no new production dependency)"},
        "validation_use": "VALIDATION scores the TRAIN-fitted CHEAP and RICH once; there are no alternatives to select between.",
    }


# ====================================================================== decision rules
def decision_rules() -> Dict[str, Any]:
    return {
        "artifact": "R4-00-DECISION-RULES",
        "primary_endpoint": {
            "name": "capture",
            "per_trace": "capture_w(P) = (B_A,w - B_P,w) / (B_A,w - B_Bel,w), B = decode miss bytes at 1.5 GiB",
            "aggregate": "capture(P) = sum_w (B_A,w - B_P,w) / sum_w (B_A,w - B_Bel,w) over all traces of the split (pooled bytes, exact rational arithmetic)",
            "invalid_if": "pooled denominator sum_w (B_A,w - B_Bel,w) <= 0, or any budget violation (resident bytes > budget after any served event)",
            "undefined_workload": "if B_A,w - B_Bel,w <= 0 for a workload, its per-workload capture is UNDEFINED and counts as failing rule C",
            "no_capping": "capture is not capped; values above 1 or below 0 are reported as computed",
            "report": ["aggregate capture", "absolute miss-byte reduction", "per-workload capture", "sign of improvement per workload"],
            "non_gating_reports": ["number of EVAL workloads on which CHEAP beats the best comparator", "block-bootstrap 90% interval of pooled capture over contiguous 64-token decode blocks (seed 0, 2000 resamples)",
                                   "VALIDATION comparator captures with and without r2-11-coding-v1", "REPLICATE_CHECK (seed-43) captures, report-only", "teacher-agreement shift diagnostic"],
        },
        "secondary_endpoints": ["misses per token", "miss bytes per token", "evictions", "reloads", "per-layer effects", "per-workload effects", "resident-set turnover",
                                "zero-cost ms/token = kappa_low * (B_A - B_P) / tokens / 1e6 with kappa_low = 0.3792 ms/MB (Research 3 Phase 0 transfer mapping)"],
        "secondary_rule": "Secondary endpoints are descriptive. None may be promoted to primary after EVAL.",
        "thresholds": {"viability": "1/4", "incremental_margin": "3/100", "workload_floor": "0", "rich_gate": "1/4"},
        "rules": {
            "A_operational_viability": "capture_VAL(CHEAP) >= 1/4 AND capture_EVAL(CHEAP) >= 1/4. A value in [0.20, 0.25) is a FAIL.",
            "B_incremental_value": "capture_EVAL(CHEAP) >= max(1/4, max(capture_EVAL(NUE_X), capture_EVAL(DECAYED_COUNT)) + 3/100), comparators replayed under the same Research 4 replay",
            "C_workload_consistency": "capture_w(CHEAP) >= 0 for every EVAL workload w",
            "RICH_information_gate": "capture_VAL(RICH) >= 1/4 AND capture_EVAL(RICH) >= 1/4",
        },
        "classification_order": [
            ["integrity failure (A not reproducing its frozen TRAIN/VALIDATION reference, budget violation, invalid denominator, any frozen identity mismatch)", "R4_CFDC_INVALID"],
            ["A and B and C", "R4_CFDC_CHEAP_VIABLE"],
            ["A and B and not C", "R4_CFDC_HETEROGENEOUS_NOT_VIABLE"],
            ["A and not B", "R4_CFDC_CHEAP_NOT_INCREMENTAL"],
            ["not A and RICH gate passes", "R4_CFDC_CAUSAL_INFORMATION_PRESENT_CHEAP_NOT_DEMONSTRATED"],
            ["not A and RICH gate fails", "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"],
        ],
        "companion_labels": {
            "COMPARATOR_REACHES_VIABILITY": "reported alongside the class if NUE_X or DECAYED_COUNT has capture >= 1/4 on both VALIDATION and EVAL; it qualifies, never upgrades, the CHEAP class",
            "C_status": "rule C is evaluated and reported in every class",
            "RICH_status": "the RICH gate is evaluated and reported in every class",
        },
        "label_meaning": "R4_CFDC_CAUSAL_STATE_INSUFFICIENT refers to the frozen feature vocabulary under RICH; if COMPARATOR_REACHES_VIABILITY is also present, the report must say that a frozen comparator reached the 25% level while the learned policies did not.",
        "validation_role": "VALIDATION is used for no fitting, selection or threshold choice; a failed VALIDATION score triggers no change before EVAL.",
        "integrity_preconditions": [
            "A reproduces its frozen decode miss bytes and misses on every TRAIN trace (R3-05 R4 DEV_RESULTS) and every VALIDATION trace (R2-11 eval_cells, 1.5 GiB)",
            "NUE_X reproduces its frozen R3-05 R4 DEV_RESULTS rows on every TRAIN trace",
            "BeladyGlobal reproduces the frozen R3-05 R2 decode miss bytes on the Research 3 canonical stream (r2.py replay)",
            "every trace hash equals DATA_SPLITS before it is read",
        ],
        "eval_audit": "EVAL is scored by one script whose SHA-256, the fitted-model hashes and the complete VALIDATION scores are committed in the Phase 1 lock before EVAL is read; the EVAL run is executed once and its output log is hashed; any re-execution is reported.",
        "rich_interpretation": "If RICH fails the gate, the tested causal state representation does not contain enough generalizable information to support the CHEAP target. No new feature set, learner or rescue experiment follows inside Research 4.",
        "eval_opening": "EVAL is opened exactly once, after the TRAIN fit of CHEAP and RICH and the VALIDATION scores are written and hash-locked, whatever the VALIDATION outcome; all frozen arms (A, BELADY, NUE_X, DECAYED_COUNT, CHEAP, RICH) are scored in that single opening.",
        "authorized_next_step_if_viable": {
            "only": "standalone CHEAP policy-cost microbenchmark (no runtime implementation)",
            "later_gate": "estimated benefit >= 10 ms/token at kappa_low AND benefit / measured policy-computation cost >= 2.0",
            "runtime_implementation": "may be proposed only after that later gate passes",
        },
        "anti_rescue": {
            "one_experiment": True,
            "forbidden": ["second feature round", "new model family", "threshold relaxation", "new training corpus", "new EVAL corpus", "EVAL-driven feature engineering",
                          "EVAL-driven hyperparameter tuning", "post-hoc success metric", "one more candidate", "re-opening EVAL"],
            "on_failure": "close the tested causal-retention direction for this regime; any fundamentally different idea is a new research question with its own Phase 0",
        },
    }


# ====================================================================== protocol
def protocol() -> Dict[str, Any]:
    return {
        "artifact": "R4-00-PROTOCOL",
        "experiment_id": "R4-00-causal-eviction-distillation", "reader_facing_name": "Causal Future-Distance Distillation", "shorthand": "CFDC",
        "question": "How much of the clairvoyant expert-eviction optimum can be recovered by a cheap causal policy using only information available at eviction-decision time?",
        "specific_question": "Can a compact deployable eviction rule, distilled offline from clairvoyant next-use supervision but evaluated causally under its own cache state, recover enough of the frozen Belady miss-byte headroom to justify any later runtime implementation?",
        "regime": {"model": "Qwen3-30B-A3B-Q4_K_M (frozen identity of the traces)", "budget_bytes": BUDGET_BYTES, "replay": "offline, integer-exact, routing traces only"},
        "phases": [
            "Phase 0 (this commit): freeze protocol; EVAL LOCKED; no replay, training or scoring.",
            "Phase 1: implement the frozen replay/feature/teacher/learner code; synthetic causality tests; verify A fidelity on TRAIN and VALIDATION; extract TRAIN labels; fit CHEAP and RICH on TRAIN; score VALIDATION once; hash-lock fit and VALIDATION scores.",
            "Phase 2: open EVAL once; score all frozen arms; apply DECISION_RULES; classify; close.",
        ],
        "prohibitions": ["no model inference", "no runtime change", "no new traces", "no EVAL access before the Phase 1 lock", "no change to Research 3 artifacts"],
        "research3_preservation": {"classification": R3_CLASSIFICATION, "closure_commit": R3_CLOSURE_COMMIT, "closure_freeze": R3_CLOSURE_FREEZE, "paper_commit": R3_PAPER_COMMIT,
                                   "rule": "Research 4 is additive and does not reinterpret Research 3 results."},
        "source": {"branch": BRANCH, "source_r3_head": SOURCE_R3_HEAD},
    }


# ====================================================================== build / verify
def contract_objects() -> Dict[str, Dict[str, Any]]:
    return {"PROTOCOL.json": protocol(), "DATA_SPLITS.json": data_splits(), "FEATURE_CONTRACT.json": feature_contract(),
            "BASELINES.json": baselines(), "MODEL_CONTRACT.json": model_contract(), "DECISION_RULES.json": decision_rules()}


def contract_digest(files: Dict[str, str]) -> str:
    return sha_bytes(canon({k: files[k] for k in sorted(files)}))


def freeze_record() -> Dict[str, Any]:
    files = {n: sha_file(HERE / n) for n in CONTRACT_JSON + CONTRACT_MD}
    ds = json.loads((HERE / "DATA_SPLITS.json").read_text(encoding="utf-8"))
    rec = {
        "artifact": "R4-00-FREEZE", "state": STATE, "eval_state": EVAL_STATE, "branch": BRANCH, "source_r3_head": SOURCE_R3_HEAD,
        "research3": {"classification": R3_CLASSIFICATION, "closure_commit": R3_CLOSURE_COMMIT, "closure_freeze": R3_CLOSURE_FREEZE, "paper_commit": R3_PAPER_COMMIT},
        "files_sha256": files, "contract_digest": contract_digest(files),
        "bound_code_lf_sha256": {p: committed_lf_sha(p) for p in BOUND_CODE},
        "data_hashes": {s: [t["trace_sha256"] for t in ds["splits"][s]["traces"]] for s in ds["splits"]},
        "feature_vocabulary": [f[1] for f in FEATURES],
        "thresholds": decision_rules()["thresholds"], "endpoint": decision_rules()["primary_endpoint"]["aggregate"],
        "outcome_metrics_present": False,
    }
    rec["freeze_sha256"] = sha_bytes(canon({k: v for k, v in rec.items() if k != "freeze_sha256"}))
    return rec


def build() -> str:
    for name, obj in contract_objects().items():
        (HERE / name).write_text(dumps(obj), encoding="utf-8")
    fz = freeze_record(); (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8")
    return fz["freeze_sha256"]


def research3_unchanged() -> List[str]:
    p = []
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", SOURCE_R3_HEAD, "--", "experiments/R3-*", "paper/research3"], capture_output=True, text=True, check=True)
    if r.stdout.strip(): p.append(f"Research 3 files differ from {SOURCE_R3_HEAD[:7]}: {r.stdout.split()[:5]}")
    r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "experiments/R3-*", "paper/research3"], capture_output=True, text=True, check=True)
    if r.stdout.strip(): p.append(f"uncommitted Research 3 changes: {r.stdout.split()[:5]}")
    fr = json.loads((EXP / "R3-final-closure/FINAL_RESULT.json").read_text())
    if fr.get("classification") != R3_CLASSIFICATION: p.append("Research 3 classification differs")
    return p


def outcome_files() -> List[str]:
    return sorted(str(p.relative_to(HERE)) for p in HERE.rglob("*") if p.is_file() and any(t in p.name.upper() for t in FORBIDDEN_OUTCOME_NAMES))


def verify() -> List[str]:
    p: List[str] = []
    expected = contract_objects()
    for name, obj in expected.items():
        on_disk = json.loads((HERE / name).read_text(encoding="utf-8"))
        if on_disk != json.loads(dumps(obj)): p.append(f"{name} differs from the frozen definition")
    fz = json.loads((HERE / "FREEZE.json").read_text(encoding="utf-8"))
    if fz["freeze_sha256"] != sha_bytes(canon({k: v for k, v in fz.items() if k != "freeze_sha256"})): p.append("FREEZE self-hash")
    for n, h in fz["files_sha256"].items():
        if sha_file(HERE / n) != h: p.append(f"{n} hash differs from FREEZE")
    if fz["contract_digest"] != contract_digest(fz["files_sha256"]): p.append("contract digest")
    for path, h in fz["bound_code_lf_sha256"].items():
        if lf_sha((REPO / path).read_bytes()) != h: p.append(f"bound code {path} changed")
    if (fz["state"], fz["eval_state"], fz["outcome_metrics_present"]) != (STATE, EVAL_STATE, False): p.append("state / EVAL lock")
    if outcome_files(): p.append(f"outcome artifacts present: {outcome_files()}")
    p += research3_unchanged()
    return p


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["build", "verify"]); a = ap.parse_args(argv)
    if a.cmd == "build":
        print("freeze_sha256", build()); return 0
    pr = verify(); print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]; return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
