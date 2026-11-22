"""Research 5 Phase 0 -- Exposed Transfer Stall (ETS): contract builder and verifier.

Phase 0 only. This module writes and verifies the frozen prospective protocol for Research 5. It contains NO runtime code,
launches nothing, opens no evaluation prompt, and creates no outcome metric. Every prior-evidence number it records is read
from a frozen artifact, with that artifact's LF-normalised SHA-256, at the source HEAD below.

    python phase0.py build    # (re)write the JSON contract and FREEZE.json from the definitions below
    python phase0.py verify   # re-check the contract, the freeze digest, the evaluation lock, the runtime-audit anchors
                              # and that no Research 1-4 frozen file changed since the source HEAD
"""
from __future__ import annotations

import argparse, functools, hashlib, json, random, subprocess, sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

STATE = "R5_ETS_PHASE0_FROZEN"
EVAL_STATE = "LOCKED"
IMPLEMENTATION_STATE = "NOT_STARTED"
SOURCE_HEAD = "8493320cdc034d44896cce910d559c5484076f6d"   # main when Research 5 Phase 0 was written
R4_CLASSIFICATION = "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"
R3_CLASSIFICATION = "R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME"
GIB = 1024 ** 3
BUDGET_PRIMARY = 1610612736          # 1.5 GiB: Research 3/4 primary budget, strongest miss pressure
BUDGET_SECONDARY = 4 * GIB            # 4 GiB: Research 2 runtime primary budget; no-harm check only
SCHEDULE_SEED = 20261122

CONTRACT_JSON = ["PROTOCOL.json", "RUNTIME_AUDIT.json", "COMPARATORS.json", "WORKLOADS.json", "ENDPOINTS.json",
                 "INSTRUMENTATION.json", "DECISION_RULES.json", "SCHEDULE.json", "PROVENANCE.json"]
CONTRACT_MD = ["README.md", "PREREGISTRATION.md", "CLAIM_BOUNDARIES.md", "REVIEW.md"]
FORBIDDEN_OUTCOME_NAMES = ["RESULT", "EVAL_OPENED", "HEADROOM_MEASURED", "CLASSIFICATION.json", "TIMING", "MECHANISM_DATA"]
# Research 1-4 frozen trees: none of them may differ from SOURCE_HEAD.
FROZEN_PATHSPECS = ["experiments/EXP-*", "experiments/R2-*", "experiments/R3-*", "experiments/R4-*", "experiments/INDEX.md",
                    "experiments/index.json", "paper", "research2", "RESEARCH_2_FINAL_CLOSURE.json", "RESEARCH_2_FINAL_CLOSURE.md",
                    "workloads", "manifests"]


class Phase0Error(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def sha_file(p: Path) -> str: return sha_bytes(p.read_bytes())
def lf_sha(b: bytes) -> str: return sha_bytes(b.replace(b"\r\n", b"\n"))
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


@functools.lru_cache(maxsize=None)
def at_head(rel: str) -> bytes:
    """Content of a file as committed at SOURCE_HEAD (independent of the working tree and of later commits)."""
    return subprocess.run(["git", "-C", str(REPO), "show", f"{SOURCE_HEAD}:{rel}"], capture_output=True, check=True).stdout


def jhead(rel: str) -> Any:
    return json.loads(at_head(rel).decode("utf-8"))


def ref(rel: str) -> Dict[str, str]:
    return {"path": rel, "lf_sha256_at_source_head": lf_sha(at_head(rel))}


def dig(o: Any, path: str) -> Any:
    for k in path.split("/"):
        o = o[int(k)] if isinstance(o, list) else o[k]
    return o


# ================================================================= prior evidence (read, never typed)
EVIDENCE: List[Tuple[str, str, str, str]] = [
    # (id, artifact, json path, meaning)
    ("r2_12_4gib_delta_tps", "experiments/R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json",
     "official_primary_statistics/primary_aggregate_statistic/observed_mean_delta_tps", "Research 2 real runtime: PredictedProtect - B_selected tokens/s at 4 GiB"),
    ("r2_12_4gib_ci_lower", "experiments/R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json",
     "official_primary_statistics/bootstrap_95ci/lower", "lower 95% bound of the above"),
    ("r2_12_4gib_ci_upper", "experiments/R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json",
     "official_primary_statistics/bootstrap_95ci/upper", "upper 95% bound of the above"),
    ("r2_12_classification", "experiments/R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json",
     "primary_classification/classification", "Research 2 runtime validation outcome"),
    ("r3_03b_predictor_us_per_call", "experiments/R3-03B-final-evidence/FINAL_RESULT.json",
     "performance/microbench_c_predictor_us_per_call", "predictor cost per call (microbenchmark)"),
    ("r3_03b_O_ms_per_token", "experiments/R3-03B-final-evidence/FINAL_RESULT.json", "mean_O_ms_per_token", "in-situ predictor overhead O"),
    ("r3_03b_P_ms_per_token", "experiments/R3-03B-final-evidence/FINAL_RESULT.json", "mean_P_ms_per_token", "in-situ policy contrast P"),
    ("r3_03b_rho", "experiments/R3-03B-final-evidence/FINAL_RESULT.json", "rho", "share of the runtime loss explained by predictor cost"),
    ("r3_04_1p5_A_ms_per_token", "experiments/R3-04-final-evidence/ARM_STATISTICS.json", "1.5_A/ms_per_token/mean",
     "Research 3 baseline A (b_selected, blocco read mode, one shard) decode ms/token at 1.5 GiB"),
    ("r3_04_1p5_A_ms_sd_launch", "experiments/R3-04-final-evidence/ARM_STATISTICS.json", "1.5_A/ms_per_token/sd", "launch-level SD of the above"),
    ("r3_04_1p5_block_sd_ms", "experiments/R3-04-closure/CLOSURE.json", "primary_1.5GiB/P_minus_A_ms_per_token/sd",
     "SD of the paired block contrast at 1.5 GiB (planning noise floor)"),
    ("r3_04_1p5_n_blocks", "experiments/R3-04-closure/CLOSURE.json", "primary_1.5GiB/P_minus_A_ms_per_token/n_blocks", "blocks behind that SD"),
    ("r3_04_1p5_P_minus_A_ms", "experiments/R3-04-closure/CLOSURE.json", "primary_1.5GiB/P_minus_A_ms_per_token/mean", "Research 3 net runtime loss at 1.5 GiB"),
    ("r3_belady_miss_reduction_pct_1p5", "experiments/R3-05-economic-retention/r2-belady-ceiling/RESULTS.json",
     "budgets/1.5/mechanism/relative_miss_reduction_pct", "clairvoyant miss reduction relative to baseline at 1.5 GiB"),
    ("r3_classification", "experiments/R3-final-closure/FINAL_RESULT.json", "classification", "Research 3 closure"),
    ("r4_classification", "experiments/R4-02-cfdc-prospective-eval/FINAL_CLASSIFICATION.json", "classification", "Research 4 closure"),
    ("r4_eval_capture_rich", "experiments/R4-02-cfdc-prospective-eval/EVAL_RESULTS.json", "aggregate/RICH/capture_float", "best tested causal policy, held-out capture"),
    ("r4_eval_capture_cheap", "experiments/R4-02-cfdc-prospective-eval/EVAL_RESULTS.json", "aggregate/CHEAP/capture_float", "distilled cheap policy, held-out capture"),
    ("exp025_1p5_measured_ms_per_token", "experiments/EXP-025-performance-model/processed/prediction.json", None,
     "Research 1 performance model: measured ms/token at 1.5 GiB (asincrono read path)"),
]


def exp025_values() -> Dict[str, float]:
    import csv, io
    rows = list(csv.DictReader(io.StringIO(at_head("experiments/EXP-025-performance-model/results.csv").decode())))
    ms = {r["label"]: float(r["measured_ms"]) for r in rows}
    txt = at_head("experiments/EXP-025-performance-model/README.md").decode("utf-8")
    import re
    m = re.search(r"\|\s*`compute`\s*\|\s*([0-9.]+)\s*\|", txt)
    return {"measured_ms_per_token_1.5G": ms["1.5G"], "compute_coefficient_ms_per_token": float(m.group(1))}


def exp004_values() -> Dict[str, float]:
    import csv, io
    rows = list(csv.DictReader(io.StringIO(at_head("experiments/EXP-004-async-store/results.csv").decode())))
    return {r["config"]: float(r["gen_tok_s_median"]) for r in rows}


@functools.lru_cache(maxsize=None)
def _provenance() -> str:
    return json.dumps(_provenance_obj())


def provenance() -> Dict[str, Any]:
    return json.loads(_provenance())


def _provenance_obj() -> Dict[str, Any]:
    vals = []
    for eid, art, path, meaning in EVIDENCE:
        if path is None:
            continue
        vals.append({"id": eid, "artifact": ref(art), "field": path, "value": dig(jhead(art), path), "meaning": meaning})
    e25 = exp025_values(); e04 = exp004_values()
    vals.append({"id": "exp025_1p5_measured_ms_per_token", "artifact": ref("experiments/EXP-025-performance-model/results.csv"),
                 "field": "label=1.5G/measured_ms", "value": e25["measured_ms_per_token_1.5G"],
                 "meaning": "Research 1 performance model: measured decode ms/token at 1.5 GiB on the asincrono LRU path"})
    vals.append({"id": "exp025_compute_coefficient", "artifact": ref("experiments/EXP-025-performance-model/README.md"),
                 "field": "fitted NNLS coefficient `compute`", "value": e25["compute_coefficient_ms_per_token"],
                 "meaning": "fitted compute intercept (ms/token); collinear with sync_nodes, contextual only"})
    for cfg in ("budget=1.5G mode=sincrono", "budget=1.5G mode=asincrono", "budget=4G mode=sincrono", "budget=4G mode=asincrono"):
        vals.append({"id": "exp004_" + cfg.replace("budget=", "").replace(" mode=", "_").replace(".", "p"),
                     "artifact": ref("experiments/EXP-004-async-store/results.csv"), "field": f"config={cfg}/gen_tok_s_median",
                     "value": e04[cfg], "meaning": "Research 1: read-read overlap (issue-and-continue) vs synchronous reads, tokens/s"})
    return {
        "artifact": "R5-00-PROVENANCE", "source_head": SOURCE_HEAD,
        "note": "Values are read from the frozen artifacts at the source HEAD by phase0.py; none is typed by hand. They motivate and "
                "calibrate the protocol; none is an R5 result.",
        "values": vals,
        "closures": {"research2": ref("RESEARCH_2_FINAL_CLOSURE.json"), "research3": ref("experiments/R3-final-closure/FINAL_RESULT.json"),
                     "research3_freeze": ref("experiments/R3-final-closure/FREEZE.json"),
                     "research4": ref("experiments/R4-02-cfdc-prospective-eval/FINAL_CLASSIFICATION.json"),
                     "research4_freeze": ref("experiments/R4-02-cfdc-prospective-eval/FREEZE.json"),
                     "research4_paper_freeze": ref("paper/research4/PUBLICATION_FREEZE.json")},
    }


def pv(eid: str) -> Any:
    for v in provenance()["values"]:
        if v["id"] == eid:
            return v["value"]
    raise Phase0Error(eid)


# ================================================================= runtime audit (anchored in the source at SOURCE_HEAD)
AUDIT_FILES = ["patches/argine-experts.cpp", "patches/argine-store.cpp", "patches/argine-store.h", "patches/README.md", "argine/cli.py"]
FINDINGS: List[Dict[str, Any]] = [
    {"id": "F1_hook_point", "file": "patches/argine-experts.cpp", "anchors": ["bool compute_forward(ggml_compute_params * params, ggml_tensor * op) override", "if (op->op != GGML_OP_MUL_MAT_ID)"],
     "finding": "Expert transfers are driven from ExpertTraits::compute_forward on every MUL_MAT_ID node: the hook reads the router ids (op->src[2]), makes the slices resident, synchronises the threads and returns false so the stock ggml kernel computes on the filled memory. No custom kernel exists."},
    {"id": "F2_causal_knowledge_point", "file": "patches/argine-experts.cpp", "anchors": ["const ggml_tensor * ids  = op->src[2];"],
     "finding": "The expert set of layer L becomes causally known only when the router of layer L has run, i.e. immediately before the first MUL_MAT_ID node of layer L. The router of L depends on the attention output of L, so no exact knowledge of layer L experts exists while layer L-1 or the attention of L computes. Any earlier issue of a layer-L read requires prediction."},
    {"id": "F3_all_kinds_loaded_at_first_node", "file": "patches/argine-experts.cpp", "anchors": ["Tutti e tre i tensori del layer, non solo quello di questo nodo.", "const uint32_t totale = n_serve * (uint32_t) n_dove;"],
     "finding": "The first expert node of a layer issues the reads for all three weight kinds (gate, up, down) of every selected expert, to cut synchronised rounds from 144 to 48 per token."},
    {"id": "F4_wait_before_any_compute", "file": "patches/argine-experts.cpp", "anchors": ["Le letture emesse vanno attese QUI: da questo punto i byte si usano.", "if (!psx_cache_attendi(c.cache, c.store)) {"],
     "finding": "In the default asincrono mode every thread then waits for ALL reads it issued (all three kinds) before the barrier that precedes the first node's compute. Reads of the kinds consumed by the second and third node are therefore waited for before any expert compute of the layer: today there is no overlap between expert transfers and compute. Read-read overlap exists (queue depth); read-compute overlap does not."},
    {"id": "F5_async_read_layer", "file": "patches/argine-store.cpp", "anchors": ["int psx_cache_avvia(struct psx_cache * c, struct psx_store * s, struct psx_region * region,", "if (ReadFile(st.preso.h, pr.dst, (DWORD) pr.want, &got, &so.ov)) {", "} else if (GetLastError() == ERROR_IO_PENDING) {"],
     "finding": "psx_cache_avvia reserves the slot (budget accounted, slice pinned) and issues an OVERLAPPED ReadFile that proceeds without a CPU thread; psx_cache_attendi waits for all reads pending on the calling thread. A deferred wait is therefore possible without new threads, but the current API waits for all pending reads of a thread at once and releases the borrowed file handle at that point."},
    {"id": "F6_pin_lifetime", "file": "patches/argine-experts.cpp", "anchors": ["I miei pin del nodo precedente: da qui possono essere sfrattati", "psx_cache_unpin(c.cache, p.region, p.offset);"],
     "finding": "In asincrono mode each thread releases its pins at the start of the next MoE node; the later nodes re-acquire the same slices as hits. A deferred wait must keep the pins of in-flight slices across the layer's nodes (released at the next layer's first node), as the retention-policy path already does for follow-up nodes; otherwise an in-flight slice could be evicted while its DMA is running."},
    {"id": "F7_lru_touch_equivalence", "file": "patches/argine-store.cpp", "anchors": ["void psx_cache_touch(struct psx_cache * c, struct psx_region * region, size_t offset) {", "sh.order.splice(sh.order.begin(), sh.order, it->second);"],
     "finding": "psx_cache_touch applies the same LRU recency move as a hit without pinning or waiting, so a deferred-wait arm can reproduce the baseline's access sequence at the later nodes. Hit COUNTERS differ by construction (the baseline counts its re-accesses as hits); misses, miss bytes and evictions must not."},
    {"id": "F8_existing_timing_trace", "file": "patches/argine-store.cpp", "anchors": ["void arx_wait_precise(StatoThread & st) {", "The part of\n        // [issue, complete] before wait_start was hidden behind other work of\n        // this thread; the rest was not."],
     "finding": "ARGINE_RESEARCH_TRACE already records, per read, issue time, completion time (exact, or an upper bound flagged as such) and the wait interval of the issuing thread, and per MoE node the hook start and end (arx_node). Transfer latency, blocking wait, the compute gap between the nodes of a layer and the part of a transfer that elapsed before its wait are therefore measurable with the existing trace; only the per-kind wait attribution of the new arm must be added."},
    {"id": "F9_policy_path_slower_and_not_deferrable", "file": "patches/argine-experts.cpp", "anchors": ["argine: ARGINE_RETENTION_POLICY richiede ARGINE_EXPERT_READ=blocco "],
     "finding": "A retention policy (b_selected / predicted_protect) forces the blocco read mode with one shard and one ensure_many call from thread 0. Research 3 measured its baseline at about 527 ms/token at 1.5 GiB, whereas the default asincrono LRU path was measured at about 258 ms/token at 1.5 GiB in Research 1 (EXP-025). The two figures come from different builds, sessions and workloads and are NOT a paired comparison; they only establish that the asincrono LRU path is the strongest documented baseline and the one R5 must use."},
    {"id": "F10_legacy_predictive_prefetch", "file": "patches/README.md", "anchors": ["undocumented asynchronous prefetch subsystem (`argine_prefetch`, `argine_issue_prefetch`, `argine_worker_main`, gated by `ARGINE_PREFETCH`, default off)"],
     "finding": "A legacy ARGINE_PREFETCH subsystem warms mmap pages of experts PREDICTED for later layers from threads outside the ggml barrier; it is off by default, does not use the expert store, and was a demonstrated memory-safety hazard. It is prediction, not causal scheduling, and is pinned OFF in every R5 arm."},
    {"id": "F11_read_read_overlap_already_tested", "file": "patches/argine-experts.cpp", "anchors": ["Emetti e continua: la lettura parte, il thread prende la slice dopo, si", "La terza, e la piu' veloce misurata."],
     "finding": "Overlap of reads WITH EACH OTHER (issue-and-continue) was already implemented and measured in Research 1 (EXP-004: regime-dependent gain, largest at 1.5 GiB). R5 does not retest it; it is part of the baseline."},
    {"id": "F12_page_commit_on_issue_path", "file": "patches/argine-store.h", "anchors": ["`VirtualAlloc(MEM_COMMIT)` costa 0.005 ms per slice, ma non e' li' che si paga", "Windows azzera le pagine al primo accesso"],
     "finding": "Committing and zeroing fresh pages is CPU work on the issuing thread (inside the read path, EXP-015). It stays on the critical path under any wait placement and is not hideable by the R5 mechanism; the headroom bound below excludes it by construction (it is measured from completion times, not from issue cost)."},
]


def runtime_audit() -> Dict[str, Any]:
    return {
        "artifact": "R5-00-RUNTIME-AUDIT", "source_head": SOURCE_HEAD,
        "files": {f: lf_sha(at_head(f)) for f in AUDIT_FILES},
        "findings": FINDINGS,
        "capability_matrix": {
            "transfer_initiation_time": "MEASURED (arx_io issue_ns)",
            "transfer_completion_time": "MEASURED; exact when observed by arx_wait_precise, otherwise an upper bound flagged ARX_FLAG_COMPLETE_BOUND",
            "blocking_wait": "MEASURED (arx_io wait_start/wait_end per thread; arx_node hook interval on the critical path)",
            "useful_compute": "BOUNDED: the stock ggml kernel is not instrumented; compute between expert nodes is the gap between consecutive hook intervals (includes small non-expert ops such as the GLU)",
            "resident_hit_and_miss": "MEASURED (arx_access outcome h / m / w; store counters)",
            "prefetch_and_wasted_prefetch": "NOT APPLICABLE: no R5 arm prefetches; the legacy predictive prefetcher is pinned off",
            "bytes_transferred": "MEASURED (store bytes_read; arx_io bytes)",
            "per_kind_deferred_wait": "MISSING: must be added with the new arm (Phase 1)",
        },
        "overlap_opportunities": {
            "exists_causal": "Within a layer: after the router decision, the reads of the kinds consumed by the second and third expert node can stay in flight while the first (and second) node compute.",
            "does_not_exist_causally": "Across layers: the experts of layer L+1 are unknown until its router runs; issuing them earlier is prediction (Research 2-4 territory) and is excluded.",
            "not_hideable": "Page commit/zeroing on issue, the first node's own reads, and synchronisation barriers.",
        },
        "verdict": "FEASIBLE_AFTER_PREREQUISITE_IMPLEMENTATION",
        "prerequisites": [
            "store API: wait for the pending reads of one weight kind of the calling thread (others stay in flight); borrowed handle released only when no read of the thread is pending",
            "experts hook: in the new arm, keep the layer's pins until the next layer's first expert node; later nodes wait only for their own kind and LRU-touch (not re-acquire) the layer's slices in the baseline's order",
            "fail-closed mode switch ARGINE_EXPERT_WAIT = layer (baseline, default, code path unchanged) | kind (new arm); any other value aborts",
            "assert zero pending reads at the end of every graph; budget violations must stay 0",
            "trace: attribute each wait interval to the node kind awaited; count deferred reads and those already complete at their wait",
        ],
    }


# ================================================================= comparators
ISSUE_ORDER = ("A's current order, frozen: at the first expert node of a layer the slices are indexed k = 0 .. n_serve*n_kinds-1 with "
               "kind = k / n_serve over the kind list [this node's kind, then the remaining kinds in enum order gate, up, down] and expert = "
               "the k % n_serve-th selected expert in ascending id; threads claim k dynamically from one shared atomic counter. C and C0 must "
               "issue exactly this sequence (verified by a model-free test that records the claimed (k, kind, expert) sequence).")


def comparators() -> Dict[str, Any]:
    common = {"ARGINE_EXPERT_READ": "asincrono", "ARGINE_RETENTION_POLICY": "<unset>", "ARGINE_PREFETCH": "0",
              "ARGINE_EXPERT_DIRECT": "1", "ARGINE_EXPERT_PRETOUCH": "<unset>",
              "binary": "the single R5 build frozen at the implementation freeze; all arms of all campaigns use the same binary",
              "end_of_run_counters": "every launch of every arm writes one end-of-run record of store counters (misses, bytes_read, evictions, budget violations, pending reads at graph end)"}
    return {
        "artifact": "R5-00-COMPARATORS",
        "common_environment": common,
        "issue_order": ISSUE_ORDER,
        "arms": {
            "A": {"role": "baseline: current runtime behaviour", "ARGINE_EXPERT_WAIT": "layer", "ARGINE_RESEARCH_TRACE": "<unset>",
                  "definition": "today's asincrono LRU path, code path unchanged: all kinds waited for at the first expert node; later nodes release and re-acquire the slices as hits", "used_in": ["timing"]},
            "C": {"role": "causal deferred-wait scheduler (the tested intervention)", "ARGINE_EXPERT_WAIT": "kind", "ARGINE_RESEARCH_TRACE": "<unset>",
                  "definition": "issue exactly as A at the first expert node; wait only for the kind this node consumes; at each later node of the same layer wait only for that node's kind and LRU-touch the layer's slices in A's order; the layer's pins are held until the next layer's first expert node",
                  "uses_only": "the router decision of the current layer (already used by A)", "parameters": "none", "used_in": ["timing"]},
            "B": {"role": "traced baseline", "ARGINE_EXPERT_WAIT": "layer", "ARGINE_RESEARCH_TRACE": "1",
                  "definition": "A with the research trace on; supplies the one-shot headroom bound (Phase 1b, development prompts) and the mechanism reference (Phase 2)", "used_in": ["headroom", "mechanism"]},
            "C0_I": {"role": "bookkeeping control (traced, mechanism only)", "ARGINE_EXPERT_WAIT": "layer_touch", "ARGINE_RESEARCH_TRACE": "1",
                     "definition": "C's code path (pins held for the layer, later nodes LRU-touch instead of re-acquire) but every thread waits for ALL its reads at the first expert node, as A. Differs from C only in where the wait happens, so C_I - C0_I isolates overlap from cheaper bookkeeping. Never in the timing campaign, never in the classification except through G4.", "used_in": ["mechanism"]},
            "C_I": {"role": "C traced", "ARGINE_EXPERT_WAIT": "kind", "ARGINE_RESEARCH_TRACE": "1", "definition": "C with the research trace on", "used_in": ["mechanism", "conformance"]},
        },
        "trace_symmetry": "B, C0_I and C_I write the identical set of trace record types and fields (fields that do not apply are written empty), so trace overhead is the same in every traced arm; the per-arm trace overhead is reported on development prompts",
        "invariants_C_vs_A": [
            "same set of slices requested per layer, the frozen issue order, same bytes read",
            "same LRU access sequence (later-node hits replaced by psx_cache_touch in the same order); misses, miss bytes and evictions within the purity tolerance",
            "same pinned footprint (no extra residency); budget violations 0; no pending read at the end of any graph",
            "no prediction, no lookahead across layers, no new thread, no tunable parameter",
            "identical generated tokens (greedy decoding on identical weights and kernels)",
        ],
        "stronger_causal_scheduler_D": {
            "included": False,
            "reason": "The runtime offers no further causally known information before a layer's router runs (F2). A 'stronger' causal scheduler of the same information would differ only in wait placement, which C already moves to the latest correct point. Any scheduler that issues layer-L reads earlier needs predicted expert ids, which is Research 2-4 territory and is excluded. A second variant would only add multiplicity.",
        },
        "oracle_E": {
            "included": True, "implementable": False,
            "definition": "the trace-derived hideable bound H of ENDPOINTS.json, computed once from B launches on development prompts; it characterises headroom only and is never a policy, an arm or a result in favour of R5; it is loose (W spans to the last expert node although node-2 reads can only hide behind node-1 compute)",
        },
        "excluded": ["any predictor or prediction-led prefetch", "the legacy ARGINE_PREFETCH subsystem", "retention policies and the blocco read mode",
                     "changes to budgets, thread counts, read mode or kernels", "any second scheduler variant"],
    }


# ================================================================= workloads, budgets, launch
WL_FILES = {"coding": "workloads/v1/coding.json", "natural-language": "workloads/v1/natural-language.json",
            "reasoning-math": "workloads/v1/reasoning-math.json", "structured-json": "workloads/v1/structured-json.json"}
DEV_IDS = {"coding": "code-dev-1", "natural-language": "nl-dev-1", "reasoning-math": "math-dev-1", "structured-json": "json-dev-1"}
EVAL_IDS = {"coding": "code-eval-1", "natural-language": "nl-eval-1", "reasoning-math": "math-eval-1", "structured-json": "json-eval-1"}


def prompt_sha(cat: str, pid: str, split: str) -> str:
    d = jhead(WL_FILES[cat])
    hits = [p for p in d["splits"][split] if p["id"] == pid]
    if len(hits) != 1:
        raise Phase0Error(f"{pid} not unique in {split}")
    return sha_bytes(canon(hits[0]))


@functools.lru_cache(maxsize=None)
def _tracked(pid: str) -> str:
    return json.dumps(_tracked_list(pid))


def tracked_references(pid: str) -> List[str]:
    return json.loads(_tracked(pid))


def _tracked_list(pid: str) -> List[str]:
    r = subprocess.run(["git", "-C", str(REPO), "grep", "-l", "--fixed-strings", pid, SOURCE_HEAD], capture_output=True, text=True)
    return sorted(x.split(":", 1)[1] for x in r.stdout.split())


def workloads() -> Dict[str, Any]:
    return {
        "artifact": "R5-00-WORKLOADS",
        "model": {"name": "Qwen3-30B-A3B-Q4_K_M", "gguf": "Qwen_Qwen3-30B-A3B-Q4_K_M.gguf", "store": "Qwen_Qwen3-30B-A3B-Q4_K_M.psxs",
                  "identity": "file SHA-256 of gguf and psxs recorded at the implementation freeze and checked at every launch"},
        "corpus": {cat: {"file": ref(f), "split_rule": "repository workload corpus: tune on dev, claim on eval (workloads/README.md)"} for cat, f in WL_FILES.items()},
        "development": {"role": "implementation, conformance, baseline fidelity, trace-overhead report and the one-shot headroom gate; every timing comparison run on them is logged",
                        "prompts": [{"category": c, "id": DEV_IDS[c], "prompt_object_sha256": prompt_sha(c, DEV_IDS[c], "dev")} for c in WL_FILES]},
        "evaluation": {"role": "Phase 2 only. No binary of any kind (R5 or earlier) may be run on these prompts before the evaluation is opened.",
                       "prompts": [{"category": c, "id": EVAL_IDS[c], "prompt_object_sha256": prompt_sha(c, EVAL_IDS[c], "eval"),
                                    "tracked_references_at_source_head": tracked_references(EVAL_IDS[c])} for c in WL_FILES],
                       "prior_use_statement": "At the source HEAD no tracked artifact other than the workload file itself names these prompt ids, so no tracked Research 1-4 result was measured on them. Untracked historical logs cannot be excluded; none informs C, which has no parameter."},
        "budgets": {"primary": {"bytes": BUDGET_PRIMARY, "label": "1.5 GiB", "why": "Research 3/4 primary budget; strongest miss pressure; where read stall dominates (EXP-025)"},
                    "secondary": {"bytes": BUDGET_SECONDARY, "label": "4 GiB", "why": "Research 2 runtime primary budget; used only as a no-harm check"}},
        "launch": {"n_predict": 512, "ignore_eos": True, "context": 4096, "temperature": 0, "top_k": 1, "seed": 42, "threads": 12, "threads_batch": 12,
                   "process": "one fresh server process per launch", "decode_metric_source": "llama.cpp server timings predicted_ms / predicted_n",
                   "ignore_eos_why": "evaluation prompts cannot be inspected in advance; without it an early end-of-sequence would make predicted_n < 512 and invalidate every arm by design",
                   "threads_why": "EXP-027: 12 generation threads were the fastest measured setting for the 30B model; fixed, never tuned in R5"},
    }


# ================================================================= endpoints
def endpoints() -> Dict[str, Any]:
    return {
        "artifact": "R5-00-ENDPOINTS",
        "primary": {
            "name": "decode_wall_ms_per_token", "definition": "server timings predicted_ms / predicted_n for one launch (predicted_n must be 512); lower is better",
            "budget": "1.5 GiB", "arms": "C vs A (both untraced)", "unit_of_analysis": "block",
            "block_statistic": "r_b = (mean_A_b - mean_C_b) / mean_A_b, with mean over the two launches of each arm in block b (relative decode-time reduction; positive = C faster)",
            "aggregate": "mean of r_b over all 20 blocks of the primary campaign", "interval": "two-sided 95% Student t interval, df = 19",
        },
        "secondary": {
            "no_harm_4GiB": "same block statistic at 4 GiB (C vs A, 20 blocks, df = 19)",
            "per_prompt_consistency": "mean r_b per evaluation prompt at 1.5 GiB (point estimates only; no per-prompt test)",
            "purity_untraced": "end-of-run counters of every timing launch: |mean miss bytes per token C - A| / A and the same for evictions, per budget",
            "tokens_per_second": "1000 / ms_per_token per launch; reported, never used to classify",
            "prefill_ms": "descriptive only",
        },
        "critical_path_rule": "Every per-node time is taken on the critical path: a node starts when the first thread enters the hook and its store phase ends when the last thread leaves the hook (max over threads); a per-node wait is the maximum over threads of that thread's wait in that node. Thread 0 alone, means over threads and sums over threads are never used for a mechanism endpoint.",
        "mechanism": {
            "source": "traced launches only (B, C0_I, C_I); never mixed with the timing campaign; 12 blocks of 3 launches, df = 11",
            "exposed_store_ms_per_token": "sum over decode expert nodes of (max_thread hook_end - min_thread hook_start), divided by decode tokens",
            "exposed_wait_ms_per_token": "sum over decode expert nodes of max_thread(wait duration in that node), divided by decode tokens. This is the operational exposed transfer stall and the G4 endpoint.",
            "non_hook_ms_per_token": "decode wall ms/token minus exposed_store_ms_per_token (compute and other graph work; shows whether concurrent transfers slow compute)",
            "hidden_deferred_bytes_fraction": "C_I only: bytes of deferred decode reads whose completion (exact, or an upper bound) is at or before the earliest wait start of their consuming node, divided by all deferred decode read bytes. Conservative: a bounded completion counts as hidden only if the bound precedes the wait.",
            "deferred_read_bytes_per_token": "bytes of decode reads of kinds not consumed by the issuing node",
            "transfer_latency_ms": "complete_ns - issue_ns per read (distribution; exact/bound flag kept)",
            "miss_bytes_per_token, misses_per_token, evictions_per_token": "scheduling purity",
            "resident_bytes_peak, budget_violations, pending_reads_at_graph_end": "residency pressure and correctness (violations and pending reads must be 0)",
            "queue_depth": "descriptive, from per-request timestamps as in EXP-007",
            "redundant_transfers": "reads of slices already resident; must be 0",
            "prefetch_used / prefetch_unused": "not applicable: no arm prefetches",
            "bookkeeping_effect": "descriptive: exposed_store_ms_per_token(C0_I) - (B)",
        },
        "headroom_bound_H": {
            "source": "computed ONCE, after the implementation freeze, from exactly 8 B launches (each of the four development prompts twice) at 1.5 GiB on the frozen binary; every H value ever computed in R5 is reported",
            "definition": "for every decode layer event: T_tail = time from the latest completion (over threads) of a first-node-kind read to the latest completion of any read issued at the first node, clipped to the first node's critical-path wait interval; W = gap from the last thread leaving the first node's hook to the first thread entering the hook of the layer's last expert node; H_layer = min(T_tail, W). H = sum of H_layer / decode tokens, expressed as a fraction of the mean decode ms/token of the same 8 launches.",
            "interpretation": "an optimistic, loose upper bound on what moving the wait can hide (ignores interference); an opening gate only, never a result",
        },
    }


# ================================================================= instrumentation
def instrumentation() -> Dict[str, Any]:
    return {
        "artifact": "R5-00-INSTRUMENTATION",
        "existing_reused": ["arx_io (issue, completion with exact/bound flag, wait start/end, bytes)", "arx_access (h/m/w)", "arx_evict", "arx_snapshot",
                            "store counters (bytes_read, hits, misses, evictions, ns_read, ns_attesa, violazioni)"],
        "to_add_in_phase1": ["per-thread hook start/end and wait start/end for every expert node (arx_node is written by thread 0 only today)",
                             "the node kind awaited by each wait, and the deferred reads it covers",
                             "count and bytes of deferred reads already complete at their wait",
                             "pending-reads-at-graph-end counter (must be 0; fail-closed abort otherwise)",
                             "one end-of-run counter record in EVERY launch (traced or not): misses, bytes_read, evictions, violations, pending reads, ARGINE_EXPERT_WAIT value",
                             "arx_config records ARGINE_EXPERT_WAIT"],
        "symmetry": "the same record types and fields in B, C0_I and C_I",
        "not_measurable_and_how_handled": [
            "true device completion of a read is observed only when polled or waited: unobserved completions are upper bounds and are treated conservatively",
            "time inside the stock ggml kernel is not instrumented: compute windows are hook gaps and include small non-expert ops; this loosens H, which is only an opening gate",
            "tracing perturbs timing (EXP-024): traced launches are never used for the primary endpoint; traced arms are compared only with each other",
        ],
        "host_record_per_launch": ["power source (AC required)", "Windows power plan", "CPU package temperature and frequency at start and end", "NVMe temperature where readable",
                                   "Defender real-time / scheduled scan state and Windows Update state", "start and end wall-clock time"],
        "timing_campaign_trace": "off (A and C untraced)",
    }


# ================================================================= decision rules
MEI = 0.02            # minimum effect of interest: 2.0% decode-time reduction (95% lower bound)
H_GATE = 0.04         # headroom bound must reach 4.0% of decode time to open the evaluation
NO_HARM = 0.02        # 4 GiB: C may not be slower than A by more than 2.0% (95% upper bound of the slowdown)
PURITY = 0.01         # scheduling purity: |miss bytes per token, evictions per token C - A| / A <= 1.0%
HIDDEN_FLOOR = 0.25   # at least a quarter of deferred bytes must complete before their wait
ATTRIBUTION = 0.5     # at least half of C_I's reduction in exposed store time vs B must be wait reduction vs C0_I
N_BLOCKS = {"timing_1.5": 20, "timing_4": 20, "mechanism_1.5": 12}
T975 = {19: 2.093024054408263, 11: 2.200985160082949}
INFRASTRUCTURE_CAUSES = ["loss of AC power or host shutdown/reboot documented in the Windows event log",
                         "forced Windows Update or Defender scan documented in the event log", "disk full on the system drive",
                         "manual interruption by the operator, recorded before the launch result is read"]


def precision_plan() -> Dict[str, Any]:
    sd = pv("r3_04_1p5_block_sd_ms"); a = pv("r3_04_1p5_A_ms_per_token")
    rel_sd = sd / a; t = T975[19]; hw = t * rel_sd / N_BLOCKS["timing_1.5"] ** 0.5
    se = rel_sd / N_BLOCKS["timing_1.5"] ** 0.5
    from statistics import NormalDist
    nd = NormalDist()
    return {"reference_block_contrast_sd_ms": sd, "reference_A_ms_per_token": a, "relative_block_sd": rel_sd, "n_blocks": N_BLOCKS["timing_1.5"],
            "t_975_df19": t, "expected_half_width_relative": hw,
            "approx_power_primary_true_3pct": nd.cdf((0.03 - MEI) / se - t),
            "approx_power_primary_true_4pct": nd.cdf((0.04 - MEI) / se - t),
            "approx_pass_prob_no_harm_true_0": nd.cdf((NO_HARM - 0.0) / se - t),
            "risk_statement": "The reference SD comes from the Research 3 blocco path (about 527 ms/token); the asincrono path is faster and its relative SD may be larger. If so, power falls; block counts are nevertheless fixed now and are not changed after any R5 data exist. A development A-vs-A noise estimate is reported in Phase 1 for information only.",
            "note": "planning only; normal approximation with the t critical value; no R5 data are used"}


def decision_rules() -> Dict[str, Any]:
    return {
        "artifact": "R5-00-DECISION-RULES",
        "thresholds": {"minimum_effect_of_interest": MEI, "headroom_gate": H_GATE, "no_harm_4GiB": NO_HARM, "scheduling_purity": PURITY,
                       "hidden_deferred_bytes_floor": HIDDEN_FLOOR, "mechanism_attribution_share": ATTRIBUTION},
        "threshold_justification": {
            "MEI_2pct": "practical relevance plus measurement scale: a decode-time reduction below 2% would not justify carrying a runtime change, and 2% is the frozen instrumentation-cost scale of EXP-024; it is about twice the expected 95% half-width of the paired primary interval (precision_plan)",
            "H_gate_4pct": "twice the MEI; realised hiding is a fraction of this optimistic bound, so below 4% there is no realistic way to pass the primary gate. Failing it only closes the direction early.",
            "no_harm_2pct": "the same practical scale as the MEI: C must not trade a gain at 1.5 GiB for a loss of comparable size where misses are rarer; with 20 blocks a harmless C passes with high probability (precision_plan)",
            "purity_1pct": "C must be a scheduling change; a larger change in miss bytes or evictions would mean residency changed and the effect could not be attributed to overlap",
            "hidden_floor_25pct": "a mechanism in which fewer than a quarter of the deferred bytes arrive before they are needed is not a meaningful overlap",
            "attribution_50pct": "at least half of the reduction of time inside the store hook must come from waiting less, not from cheaper bookkeeping",
        },
        "precision_plan": precision_plan(),
        "gates": {
            "G1_implementation": "Phase 1a delivers C and C0 with: identical tokens to A on all four development prompts at 1.5 and 4 GiB; 0 budget violations; 0 pending reads at graph end; 0 redundant transfers; the frozen issue order (model-free test); scheduling purity within 1% on development prompts; model-free store tests passing; all mechanism metrics computable; baseline fidelity: A on the R5 build produces tokens identical to the pre-R5 runtime (llama-research-v5) on the development prompts and its mean decode ms/token is not more than 2% slower (point estimate over 4 prompts x 2 launches each). Otherwise R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED.",
            "IMPLEMENTATION_FREEZE": "before G2: source hashes, binary hash, model and store hashes, and the list of implementation choices (wait primitive, handle-release point, barrier placement, pin-release point, touch order, trace record layout) are frozen; no change afterwards; every development timing comparison run before the freeze is appended to a committed development log",
            "G2_headroom": "one-shot: H from the 8 pre-declared B launches on the frozen binary >= 0.04. Otherwise R5_NO_MEANINGFUL_OVERLAP_HEADROOM and the evaluation is never opened. H is not recomputed.",
            "G3_primary": "95% lower bound of the primary aggregate >= 0.02",
            "G4_mechanism": "mechanism campaign (12 blocks, df = 11): (a) 95% upper bound of the mean block difference exposed_wait_ms_per_token(C_I) - exposed_wait_ms_per_token(C0_I) < 0; (b) mean hidden_deferred_bytes_fraction(C_I) >= 0.25; (c) mean[exposed_wait(C0_I) - exposed_wait(C_I)] >= 0.5 x mean[exposed_store(B) - exposed_store(C_I)] whenever the latter is positive",
            "G5_no_harm": "4 GiB: 95% upper bound of the mean block relative slowdown (-r_b) <= 0.02",
            "G6_consistency": "at least 3 of the 4 evaluation prompts have a positive mean r_b at 1.5 GiB",
            "G7_purity": "in the mechanism campaign (C_I vs B) and in both timing campaigns (C vs A, end-of-run counters): relative difference of mean miss bytes per token and of mean evictions per token <= 0.01; budget violations 0",
            "INTEGRITY": "every counted launch: 512 decode tokens; token digest equal to the digest of that prompt in every other arm; runtime, model and store hashes equal to the implementation freeze; no abort; server timings present; end-of-run counter record present; ARGINE_EXPERT_WAIT echoed as expected; AC power",
        },
        "blame_rules": {
            "rule": "A failed or invalid launch of C, C_I or C0_I is attributed to the intervention unless it matches a pre-listed infrastructure cause with documentary evidence; a failed or invalid launch of A or B is attributed to the campaign.",
            "infrastructure_causes": INFRASTRUCTURE_CAUSES,
            "reruns": "a slot failed for an infrastructure cause may be rerun once (attempt+1); a correctness failure of C/C_I/C0_I (token digest, budget, pending reads, abort) is never rerun; a second failure of the same slot aborts the campaign",
            "terminal": "R5_INVALID is final: the evaluation is not reopened, no new campaign is started under this contract",
        },
        "classification_order": [
            ["G1 fails (Phase 1a), or any Phase 2 launch of C, C_I or C0_I has a correctness failure or a non-infrastructure failure", "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED"],
            ["G2 fails (Phase 1b)", "R5_NO_MEANINGFUL_OVERLAP_HEADROOM"],
            ["Phase 2 campaign aborted or integrity failure of A/B launches", "R5_INVALID"],
            ["G7 fails", "R5_CONFOUNDED_NOT_PURE_SCHEDULING"],
            ["G3 and G4 and G5 and G6", "R5_POSITIVE_RUNTIME_IMPROVEMENT"],
            ["G3 and G4 and not (G5 and G6)", "R5_PRIMARY_GAIN_NOT_ROBUST"],
            ["G3 and not G4", "R5_RUNTIME_CHANGE_WITHOUT_MECHANISM"],
            ["not G3 and G4", "R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN"],
            ["not G3 and not G4", "R5_NO_REALISED_OVERLAP_NO_GAIN"],
        ],
        "label_meaning": {
            "R5_POSITIVE_RUNTIME_IMPROVEMENT": "the only outcome that counts as a demonstrated real-runtime improvement (and the only one that can unlock HoPi)",
            "R5_PRIMARY_GAIN_NOT_ROBUST": "faster at 1.5 GiB but harmful at 4 GiB or not consistent across prompts: not an improvement",
            "R5_RUNTIME_CHANGE_WITHOUT_MECHANISM": "a timing change that the deferred-wait mechanism does not explain (for example cheaper bookkeeping): not attributed to the R5 hypothesis, not an improvement",
            "R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN": "stall was hidden but end-to-end decode did not improve by the MEI: falsifies the runtime hypothesis",
            "R5_NO_REALISED_OVERLAP_NO_GAIN": "headroom existed in the bound but the implementation did not hide stall: falsifies the mechanism hypothesis",
            "R5_NO_MEANINGFUL_OVERLAP_HEADROOM": "not enough hideable transfer latency on the critical path: the direction is closed before evaluation",
            "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED": "the deferred wait cannot be implemented correctly, stably or measurably in this runtime",
            "R5_CONFOUNDED_NOT_PURE_SCHEDULING": "C changed residency beyond tolerance: no attribution possible",
            "R5_INVALID": "the campaign cannot be interpreted; final",
        },
        "anti_rescue": [
            "only R5_POSITIVE_RUNTIME_IMPROVEMENT may be described as an improvement; every other label is reported as a negative or uninterpretable outcome, never as a partial success",
            "no secondary endpoint, subset, prompt, budget, thread count or tracing configuration can upgrade the classification",
            "no second scheduler variant, no parameter, no new prompt and no new budget after the freeze",
            "the oracle bound H is never reported as an achievable gain",
            "the evaluation is opened once; reruns only under blame_rules",
            "no optional stopping: block counts are constants of this contract",
            "no outlier removal, imputation, reordering or data-driven exclusion",
        ],
        "hopi_gate": "HoPi M9+ stays paused unless the classification is R5_POSITIVE_RUNTIME_IMPROVEMENT. Every other label, including a clean negative, does not unlock HoPi; it requires the next Argine direction to be identified.",
    }


# ================================================================= schedule (frozen now, before any data)
def schedule() -> Dict[str, Any]:
    rng = random.Random(SCHEDULE_SEED)
    evp = [EVAL_IDS[c] for c in WL_FILES]

    def prompts_for(n: int) -> List[str]:
        per = n // len(evp)
        ps = [p for p in evp for _ in range(per)]
        rng.shuffle(ps)
        return ps

    def timing(n: int) -> List[Dict[str, Any]]:
        ps = prompts_for(n)
        first = ["A"] * (n // 2) + ["C"] * (n - n // 2)
        rng.shuffle(first)
        return [{"block": i + 1, "prompt": p, "order": [f, ("C" if f == "A" else "A"), ("C" if f == "A" else "A"), f]} for i, (p, f) in enumerate(zip(ps, first))]

    def mechanism(n: int) -> List[Dict[str, Any]]:
        import itertools
        perms = [list(x) for x in itertools.permutations(["B", "C0_I", "C_I"])]
        orders = [perms[i % len(perms)] for i in range(n)]
        rng.shuffle(orders)
        ps = prompts_for(n)
        return [{"block": i + 1, "prompt": p, "order": o} for i, (p, o) in enumerate(zip(ps, orders))]

    camp = {
        "mechanism_1.5GiB": {"budget_bytes": BUDGET_PRIMARY, "arms": ["B", "C0_I", "C_I"], "blocks": mechanism(N_BLOCKS["mechanism_1.5"])},
        "timing_1.5GiB": {"budget_bytes": BUDGET_PRIMARY, "arms": ["A", "C"], "blocks": timing(N_BLOCKS["timing_1.5"])},
        "timing_4GiB": {"budget_bytes": BUDGET_SECONDARY, "arms": ["A", "C"], "blocks": timing(N_BLOCKS["timing_4"])},
    }
    return {
        "artifact": "R5-00-SCHEDULE", "seed": SCHEDULE_SEED, "campaign_order": ["mechanism_1.5GiB", "timing_1.5GiB", "timing_4GiB"],
        "design": "timing: palindromic 4-launch blocks (XYYX), balanced first arm, prompts balanced across blocks; mechanism: 3-launch blocks, each of the 6 arm orders used twice, prompts balanced",
        "warmup": "one uncounted launch per campaign with the first arm of block 1 and the identical environment, then 60 s cooldown; recorded, never used",
        "cooldown_between_launches_s": 10,
        "host_conditions": "AC power; same Windows power plan; background applications closed; Defender scheduled scans and Windows Update paused for the campaign; host record per launch (INSTRUMENTATION.json); no data-driven exclusion",
        "campaigns": camp,
        "launch_counts": {k: len(v["blocks"]) * len(v["blocks"][0]["order"]) for k, v in camp.items()},
    }


# ================================================================= protocol
def protocol() -> Dict[str, Any]:
    return {
        "artifact": "R5-00-PROTOCOL", "schema": "r5-00-protocol-v1", "state": STATE, "eval_state": EVAL_STATE,
        "implementation_state": IMPLEMENTATION_STATE, "source_head": SOURCE_HEAD, "short_name": "ETS (exposed transfer stall)",
        "question": ("In the Argine expert-store runtime (Qwen3-30B-A3B-Q4_K_M, asynchronous LRU read path, 1.5 GiB expert budget), does a causal "
                     "deferred-wait scheduler -- which waits for each expert-weight kind at the node that consumes it instead of waiting for all "
                     "kinds of the layer before any expert compute, uses only the current layer's router decision, adds no prediction, and leaves "
                     "the requested slices, bytes read and residency decisions unchanged -- reduce the expert-transfer wait exposed on the decode "
                     "critical path and thereby lower end-to-end decode wall time per token by at least 2%?"),
        "hypotheses": {
            "H_primary": "C lowers decode wall ms/token relative to A at 1.5 GiB: the 95% lower bound of the mean block relative reduction is >= 2.0%.",
            "H_null": "The relative reduction is below 2.0% (including zero or a slowdown).",
            "H_mechanism": "C lowers the critical-path wait inside the expert-store hook relative to the bookkeeping-matched control C0, at least a quarter of deferred read bytes complete before they are needed, and wait reduction accounts for at least half of C's reduction of store-hook time.",
            "H_headroom": "Before evaluation, on development prompts, the one-shot trace-derived hideable bound H is at least 4% of decode time.",
            "H_no_harm": "At 4 GiB, C is not slower than A by more than 2.0% (95% upper bound).",
        },
        "falsifiers": [
            "H < 4% of decode time on development prompts (not enough transfer latency overlaps a compute window)",
            "the deferred wait cannot be implemented with identical tokens, zero budget violations, no pending reads and a faithful baseline (synchronisation or ownership prevents overlap)",
            "the mechanism campaign shows no reduction of exposed wait against C0, or fewer than a quarter of deferred bytes arrive in time (causal information arrives too late)",
            "the mechanism is present but the primary lower bound stays below 2% (hidden stall does not reach end-to-end time, or concurrent transfers slow compute)",
            "miss bytes or evictions change beyond 1% (the change is not pure scheduling)",
            "harm above 2% at 4 GiB or inconsistency across prompts",
        ],
        "phases": {
            "phase0": "this contract (frozen after independent adversarial review; see REVIEW.md)",
            "phase1a": "implementation and conformance on development prompts only: store and hook changes behind ARGINE_EXPERT_WAIT = layer | layer_touch | kind (fail-closed), model-free tests, one R5 build, gate G1, development log of every timing comparison, then the IMPLEMENTATION_FREEZE. No evaluation prompt is run by any binary.",
            "phase1b": "one-shot headroom gate G2 from 8 pre-declared B launches on the frozen binary",
            "phase2": "evaluation opened once: mechanism campaign, primary timing campaign, 4 GiB no-harm campaign, classification by DECISION_RULES.json",
        },
        "contract_files": CONTRACT_JSON + CONTRACT_MD,
        "relationship": {"research2": "predictive structure exists", "research3": "prediction-led retention fails economically in the real runtime",
                         "research4": "the tested richer causal state does not recover enough retention headroom",
                         "research5": "changes the target from avoiding transfers to hiding the latency of unavoidable transfers behind compute that the runtime already performs"},
    }


# ================================================================= freeze and verify
def contract_objects() -> Dict[str, Dict[str, Any]]:
    return {"PROTOCOL.json": protocol(), "RUNTIME_AUDIT.json": runtime_audit(), "COMPARATORS.json": comparators(),
            "WORKLOADS.json": workloads(), "ENDPOINTS.json": endpoints(), "INSTRUMENTATION.json": instrumentation(),
            "DECISION_RULES.json": decision_rules(), "SCHEDULE.json": schedule(), "PROVENANCE.json": provenance()}


def contract_digest(files: Dict[str, str]) -> str:
    return sha_bytes(canon({k: files[k] for k in sorted(files)}))


def freeze_record() -> Dict[str, Any]:
    files = {n: lf_sha((HERE / n).read_bytes()) for n in CONTRACT_JSON + CONTRACT_MD}
    rec = {
        "artifact": "R5-00-FREEZE", "state": STATE, "eval_state": EVAL_STATE, "implementation_state": IMPLEMENTATION_STATE,
        "source_head": SOURCE_HEAD, "research3_classification": R3_CLASSIFICATION, "research4_classification": R4_CLASSIFICATION,
        "files_lf_sha256": files, "contract_digest": contract_digest(files),
        "thresholds": decision_rules()["thresholds"], "primary_endpoint": endpoints()["primary"]["name"],
        "classification_labels": [r[1] for r in decision_rules()["classification_order"]],
        "evaluation_prompt_ids": [EVAL_IDS[c] for c in WL_FILES], "development_prompt_ids": [DEV_IDS[c] for c in WL_FILES],
        "outcome_metrics_present": False, "runtime_launched": False,
    }
    rec["freeze_sha256"] = sha_bytes(canon({k: v for k, v in rec.items() if k != "freeze_sha256"}))
    return rec


def build() -> str:
    for name, obj in contract_objects().items():
        (HERE / name).write_text(dumps(obj), encoding="utf-8", newline="\n")
    fz = freeze_record()
    (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8", newline="\n")
    return fz["freeze_sha256"]


def frozen_unchanged() -> List[str]:
    p = []
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", SOURCE_HEAD, "--", *FROZEN_PATHSPECS], capture_output=True, text=True, check=True)
    if r.stdout.strip():
        p.append(f"Research 1-4 frozen files differ from {SOURCE_HEAD[:7]}: {r.stdout.split()[:5]}")
    r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", *FROZEN_PATHSPECS], capture_output=True, text=True, check=True)
    if r.stdout.strip():
        p.append(f"uncommitted changes under Research 1-4 frozen paths: {r.stdout.split()[:5]}")
    for rel, want in (("experiments/R3-final-closure/FINAL_RESULT.json", R3_CLASSIFICATION), ("experiments/R4-02-cfdc-prospective-eval/FINAL_CLASSIFICATION.json", R4_CLASSIFICATION)):
        if json.loads((REPO / rel).read_text(encoding="utf-8")).get("classification") != want:
            p.append(f"{rel} classification differs")
    return p


def anchors_present() -> List[str]:
    p = []
    for f in FINDINGS:
        txt = at_head(f["file"]).decode("utf-8").replace("\r\n", "\n")
        for a in f["anchors"]:
            if a not in txt:
                p.append(f"audit anchor of {f['id']} not found in {f['file']} at {SOURCE_HEAD[:7]}")
    return p


def outcome_files() -> List[str]:
    return sorted(str(q.relative_to(HERE)) for q in HERE.rglob("*") if q.is_file() and "__pycache__" not in q.parts
                  and any(t in q.name.upper() for t in FORBIDDEN_OUTCOME_NAMES))


def verify() -> List[str]:
    p: List[str] = []
    for name, obj in contract_objects().items():
        on_disk = json.loads((HERE / name).read_text(encoding="utf-8"))
        if on_disk != json.loads(dumps(obj)):
            p.append(f"{name} differs from the frozen definition")
    fz = json.loads((HERE / "FREEZE.json").read_text(encoding="utf-8"))
    if fz["freeze_sha256"] != sha_bytes(canon({k: v for k, v in fz.items() if k != "freeze_sha256"})):
        p.append("FREEZE self-hash")
    for n, h in fz["files_lf_sha256"].items():
        if lf_sha((HERE / n).read_bytes()) != h:
            p.append(f"{n} hash differs from FREEZE")
    if fz["contract_digest"] != contract_digest(fz["files_lf_sha256"]):
        p.append("contract digest")
    if fz != json.loads(dumps(freeze_record())):
        p.append("FREEZE differs from the record rebuilt from the contract")
    if (fz["state"], fz["eval_state"], fz["outcome_metrics_present"], fz["runtime_launched"]) != (STATE, EVAL_STATE, False, False):
        p.append("state / evaluation lock")
    if outcome_files():
        p.append(f"outcome artifacts present: {outcome_files()}")
    p += anchors_present()
    p += frozen_unchanged()
    return p


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["build", "verify"]); a = ap.parse_args(argv)
    if a.cmd == "build":
        print("freeze_sha256", build()); return 0
    pr = verify(); print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]; return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
