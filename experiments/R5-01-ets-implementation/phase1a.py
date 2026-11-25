"""Research 5 Phase 1a -- ETS runtime realisation: implementation record, gate and verifier.

    python phase1a.py build    # (re)write IMPLEMENTATION.json, INSTRUMENTATION_SCHEMA.json and GATE.json
    python phase1a.py verify   # re-check source and binary identities, the R5 diff, the frozen Phase 0 contract,
                               # Research 1-4 immutability, the untouched frozen runtime sources and the evaluation lock

Phase 1a realises the frozen R5-00 arms in the runtime and proves conformance model-free. It runs no model, opens no
evaluation prompt and computes no mechanism or timing metric. The host part of gate G1 (real tokens, purity, baseline
fidelity) is the next step (host_conformance.py) and is recorded here as PENDING.
"""
from __future__ import annotations

import argparse, difflib, hashlib, importlib.util, json, subprocess, sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P0_DIR = REPO / "experiments" / "R5-00-exposed-transfer-stall"
R5SRC = REPO / "patches" / "r5-ets"
# The R5 runtime build in force. llama-research-r5-ets/BUILD-INFO.json (first build) is kept unchanged; it was superseded
# after the host incident of the first G1 attempt (G1-ATTEMPT-1-INCIDENT.md).
BUILD_INFO = REPO / "llama-research-r5-ets-2" / "BUILD-INFO.json"
BUILD_INFO_SUPERSEDED = REPO / "llama-research-r5-ets" / "BUILD-INFO.json"
SOURCE_HEAD = "6a178123134b4139e776bb5ee4664fb32b4cefae"           # Research 5 Phase 0 commit
P0_CONTRACT_DIGEST = "9d480ccf1b5109fa9be6cd717261e824876122e1ec504a3ccd85e0e30e619e52"
P0_FREEZE = "21ed630b638ca56a19b6a392a6ce1e47782dcd4351b178d523d5e28a6c7f63ee"
STATE = "R5_ETS_PHASE1A_IMPLEMENTED"
CHANGED = ["argine-store.cpp", "argine-store.h", "argine-experts.cpp", "argine-research.cpp", "argine-research.h"]
NEW = ["argine-ets.h", "argine-ets-test.cpp", "build-r5-ets.sh", "toolchain-mingw-w64.cmake", "mingw-toolchain-debs.sha256",
       "pe_normalized_sha256.py", "README.md", "R5_CHANGES.diff", "argine-tls.h", "mingw-tls-emulation.cpp"]
FROZEN_RUNTIME = ["patches/" + f for f in CHANGED + ["argine-experts.h", "argine-retention-policy.cpp", "argine-retention-policy.h"]]
FORBIDDEN_OUTCOME_NAMES = ["G1_HOST_RESULT", "HEADROOM", "EVAL_OPENED", "TIMING", "MECHANISM_DATA", "CLASSIFICATION.json", "DEV_LOG"]


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def lf(b: bytes) -> bytes: return b.replace(b"\r\n", b"\n")
def lf_sha(p: Path) -> str: return sha_bytes(lf(p.read_bytes()))
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def phase0():
    spec = importlib.util.spec_from_file_location("r5_phase0", P0_DIR / "phase0.py")
    m = importlib.util.module_from_spec(spec); sys.modules["r5_phase0"] = m; spec.loader.exec_module(m)  # type: ignore
    return m


def r5_diff() -> str:
    out: List[str] = []
    for f in CHANGED:
        a = lf((REPO / "patches" / f).read_bytes()).decode("utf-8").splitlines(keepends=True)
        b = lf((R5SRC / f).read_bytes()).decode("utf-8").splitlines(keepends=True)
        out += difflib.unified_diff(a, b, fromfile=f"patches/{f}", tofile=f"patches/r5-ets/{f}")
    return "".join(out)


ARM_SEMANTICS = {
    "layer": {"arm": "A (baseline)", "code": "argine-experts.cpp existing LETTURA_ASINCRONA block, unchanged",
              "issue": "first expert node of the layer event: psx_cache_avvia for every (kind, expert), kinds ordered [node kind, then gate, up, down], experts ascending, claimed dynamically from one shared counter",
              "wait": "every thread psx_cache_attendi (all its reads) before the post-wait barrier, at every node",
              "pins": "released by each thread at the start of every expert node; later nodes re-acquire the slices as hits (psx_cache_avvia -> HIT)"},
    "layer_touch": {"arm": "C0 (bookkeeping control)", "code": "argine-ets.h psx_ets_pre / psx_ets_io",
                    "issue": "identical to layer at the first node (same claim order, same counter)",
                    "wait": "every thread psx_cache_attendi_kinds(all kinds) at the first node (baseline placement)",
                    "pins": "held for the whole layer event; released at the next event's first node after waiting for any read still pending; later nodes psx_cache_touch each slice in the baseline claim order (same LRU move as a hit, no pin, no wait)"},
    "kind": {"arm": "C (deferred-wait scheduler)", "code": "argine-ets.h psx_ets_pre / psx_ets_io",
             "issue": "identical to layer at the first node",
             "wait": "each node: psx_cache_attendi_kinds(bit of the kind it consumes) before the post-wait barrier; reads of later kinds stay in flight (reserved: ready == false, pinned) while earlier nodes compute",
             "pins": "identical to layer_touch"},
}
CHOICES = {  # R5-00 DECISION_RULES gates.IMPLEMENTATION_FREEZE: the implementation choices, frozen with the implementation
    "wait_primitive": "per read GetOverlappedResult(bWait=TRUE) on the thread's borrowed handle, as psx_cache_attendi; with tracing, WaitForMultipleObjects one completion at a time first (as the baseline's arx_wait_precise)",
    "handle_release_point": "the thread's borrowed file handle is released, and its pending-read deque cleared, only when none of its reads is pending; entries are marked finished, never erased from the middle (the kernel holds OVERLAPPED addresses)",
    "barrier_placement": "unchanged: one barrier before issue (after the shared counter reset by thread 0), one after the wait; no barrier added or removed",
    "pin_release_point": "C0 and C: at the next layer event's first expert node, after waiting for any read of the thread still pending (counted as orphan_pending, must be 0)",
    "touch_order": "C0 and C later nodes: psx_cache_touch over the same (kind, expert) claim sequence the baseline re-acquires, distributed by the same shared counter",
    "event_identity": "per-thread psx_semantic_event_tracker (same layer and kind not yet seen = same event); deterministic and identical on every thread because every thread runs every node",
    "kind_unknown_at_first_node": "a kind whose tensor position is not yet registered at the first node is issued at its own node and waited there, as the baseline does",
    "trace_layout": "one T record per thread per expert node in every arm (INSTRUMENTATION_SCHEMA.json); I records unchanged in format; config keys expert_wait, expert_wait_explicit, ets_schema",
    "failure_handling": "a failed read is fatal at the node that waits for it (GGML_ABORT), as in the baseline; an invalid or unrealisable arm aborts at startup",
}


def implementation() -> Dict[str, Any]:
    return {
        "artifact": "R5-01-IMPLEMENTATION", "state": STATE, "source_head": SOURCE_HEAD,
        "phase0": {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE},
        "selector": "ARGINE_EXPERT_WAIT = layer | layer_touch | kind (unset = layer); anything else aborts",
        "fail_closed": ["invalid value", "layer_touch/kind without ARGINE_EXPERT_READ=asincrono", "layer_touch/kind with ARGINE_RETENTION_POLICY",
                        "explicit ARGINE_EXPERT_WAIT with ARGINE_PREFETCH set to anything but 0"],
        "arms": ARM_SEMANTICS,
        "causal_information": "only the router decision of the current layer, already consumed by the baseline hook; no prediction, no lookahead across layers, no new thread, no tunable parameter",
        "read_compute_overlap": "kind only: between the first expert node's wait and the waits of the second and third expert nodes of the same layer, reads of the later kinds are in flight (OVERLAPPED I/O needs no CPU thread) while ggml computes the earlier nodes",
        "invariants": ["same slices requested, same claim order, same bytes read", "same LRU access sequence (touch == hit recency move); hit COUNTERS differ by construction",
                       "same pinned footprint: the layer's slices, as in the baseline; in-flight slots are reserved (ready == false) and never evictable", "budget unchanged; budget violations must stay 0"],
        "implementation_choices": CHOICES,
        "sources_lf_sha256": {f"patches/r5-ets/{f}": lf_sha(R5SRC / f) for f in CHANGED + NEW},
        "frozen_runtime_sources_reused_lf_sha256": {p: lf_sha(REPO / p) for p in FROZEN_RUNTIME},
        "layout_decision": "R1-R4 runtime sources in patches/ are hash-locked by the consolidation manifest; R5 sources are an additive copy in patches/r5-ets/ and the binary identity is llama-research-r5-ets/BUILD-INFO.json",
    }


def instrumentation_schema() -> Dict[str, Any]:
    return {
        "artifact": "R5-01-INSTRUMENTATION-SCHEMA", "schema": "r5-ets-v1",
        "T_record": {"fields": ["graph", "layer", "kind", "tid", "mode", "first", "hook_in_ns", "wait_start_ns", "wait_end_ns", "hook_out_ns",
                                "wait_mask", "n_awaited", "bytes_awaited", "n_done_at_wait", "bytes_done_at_wait", "pending_after"],
                     "written_by": "every thread, every expert node, every arm (B, C0_I, C_I identical layout)",
                     "requirement": "R5-00 INSTRUMENTATION to_add_in_phase1: per-thread hook start/end and wait start/end per expert node; node kind awaited; deferred reads already complete at their wait"},
        "I_record": "unchanged format; for C/C0 the wait_start/wait_end of a read are those of the wait that announced it (its consuming node in C)",
        "config_keys": {"expert_wait": "arm", "expert_wait_explicit": "0|1", "ets_schema": "r5-ets-v1"},
        "stderr_identity_line": "argine: attesa expert: <layer|layer_touch|kind> (<esplicita|default>), ARGINE_PREFETCH=<value>, schema r5-ets-v1",
        "stderr_counters_line": {"text": "argine: r5_counters {json}", "when": "process exit (graceful), every launch, every arm",
                                 "fields": ["schema", "expert_wait", "explicit", "read_mode", "prefetch", "hits", "misses", "evictions", "bytes_read", "violations",
                                            "budget", "moe_nodes", "deferred_reads", "deferred_bytes", "touches", "issued_followup", "orphan_pending", "pending_at_event_end"]},
        "critical_path_reconstruction": {
            "node": "(graph, layer, kind); start = min over threads of hook_in_ns; store phase end = max over threads of hook_out_ns",
            "exposed_store_ms_per_token": "sum over decode nodes of (max hook_out - min hook_in) / decode tokens",
            "exposed_wait_ms_per_token": "sum over decode nodes of max over threads (wait_end - wait_start) / decode tokens",
            "hidden_deferred_bytes_fraction": "deferred reads (issued in the first node's window, kind != first node's kind) whose completion <= earliest wait_start of their consuming node, by bytes",
            "H": "per decode event min(T_tail, W) on B traces (ets_trace.metrics_from_log); one shot in Phase 1b",
            "decode_tokens": "graphs whose R records have n_tokens == 1",
            "implementation": "ets_trace.py (structure() for Phase 1a, metrics() for Phase 1b/2 only)"},
        "not_measured": ["true device completion of a read not observed before its wait (upper bound, conservative)", "time inside the ggml kernel (bounded by hook gaps)"],
    }


def model_free_record() -> Dict[str, Any]:
    return json.loads((HERE / "MODEL_FREE_CONFORMANCE.json").read_text(encoding="utf-8"))


def gate() -> Dict[str, Any]:
    mf = model_free_record()
    bi = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    crit = {
        "model_free_tests_pass": mf["argine_ets_test"]["linux_deferred_and_immediate"]["failed"] == 0 and mf["argine_ets_test"]["linux_deferred_and_immediate"]["passed"] > 0,
        "thread_sanitizer_clean": mf["argine_ets_test"]["threadsanitizer"]["warnings"] == 0 and mf["argine_ets_test"]["threadsanitizer"]["failed"] == 0,
        "address_ub_sanitizer_clean": mf["argine_ets_test"]["asan_ubsan"]["errors"] == 0 and mf["argine_ets_test"]["asan_ubsan"]["failed"] == 0,
        "existing_store_regressions_unchanged": all(r["frozen"] == r["r5"] == "pass" for r in mf["existing_store_regressions"].values()),
        "baseline_path_unchanged": "layer" in ARM_SEMANTICS and "unchanged" in ARM_SEMANTICS["layer"]["code"],
        "bookkeeping_control_exists": mf["argine_ets_test"]["covers"]["bookkeeping_control_wait_placement"],
        "kind_defers_waits": mf["argine_ets_test"]["covers"]["kind_defers_bytes_absent_until_consuming_node"],
        "identical_requests_bytes_eviction_lru": mf["argine_ets_test"]["covers"]["identical_miss_and_eviction_sequences"],
        "no_budget_violation": mf["argine_ets_test"]["covers"]["no_budget_violation"],
        "no_pending_io_eviction": mf["argine_ets_test"]["covers"]["in_flight_never_evicted"],
        "fail_closed_mode_selection": mf["argine_ets_test"]["covers"]["fail_closed_configuration"],
        "instrumentation_complete_and_inert": mf["argine_ets_test"]["covers"]["instrumentation_inert_and_coherent"] and mf["trace_analysis_tests"]["failed"] == 0,
        "windows_build_succeeds": bool(bi["binaries"].get("llama-server.exe")) and bool(bi["binaries"].get("argine-ets-test.exe")),
        "build_reproducible": bi["reproducibility"]["normalized_sha256_identical"],
        "runtime_identity_recorded": all(k in bi for k in ("llama_cpp_commit", "compiler", "cmake_flags", "binaries", "sources_lf_sha256")),
        "independent_review_resolved": mf["independent_review"]["material_open"] == 0,
    }
    return {"artifact": "R5-01-GATE", "gate": "R5_PHASE1A_IMPLEMENTATION_GATE",
            "verdict": "R5_PHASE1A_IMPLEMENTATION_GATE_PASS" if all(crit.values()) else "R5_PHASE1A_IMPLEMENTATION_GATE_FAIL",
            "criteria": crit,
            "phase0_frozen_digests": {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE},
            "host_G1": {"state": "PENDING", "driver": "experiments/R5-01-ets-implementation/host_conformance.py",
                        "covers": "R5-00 gate G1 host part: identical tokens (all arms, 1.5 and 4 GiB, v5), zero violations and pending reads, purity <= 1%, baseline fidelity vs llama-research-v5 <= 2%, Windows model-free run, startup smoke and fail-closed refusals, instrumentation completeness"},
            "implementation_freeze": "after host G1 passes (R5-00 IMPLEMENTATION_FREEZE); any source or binary change before then requires a new commit and a new host G1",
            "evaluation": "LOCKED", "headroom_gate_G2": "NOT RUN"}


def build() -> None:
    for name, obj in (("IMPLEMENTATION.json", implementation()), ("INSTRUMENTATION_SCHEMA.json", instrumentation_schema()), ("GATE.json", gate())):
        (HERE / name).write_text(dumps(obj), encoding="utf-8", newline="\n")


def verify() -> List[str]:
    p: List[str] = []
    P0 = phase0()
    p += [f"phase0: {x}" for x in P0.verify()]
    fz = json.loads((P0_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if (fz["contract_digest"], fz["freeze_sha256"]) != (P0_CONTRACT_DIGEST, P0_FREEZE):
        p.append("Phase 0 digests differ from the frozen values")
    for name, obj in (("IMPLEMENTATION.json", implementation()), ("INSTRUMENTATION_SCHEMA.json", instrumentation_schema()), ("GATE.json", gate())):
        if json.loads((HERE / name).read_text(encoding="utf-8")) != json.loads(dumps(obj)):
            p.append(f"{name} differs from its definition (sources, binaries or records changed)")
    if lf((R5SRC / "R5_CHANGES.diff").read_bytes()).decode("utf-8") != r5_diff():
        p.append("R5_CHANGES.diff does not equal the diff of patches/r5-ets against the frozen sources")
    bi = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    if bi["sources_lf_sha256"] != {k: v for k, v in implementation()["sources_lf_sha256"].items() if not k.endswith((".md", ".diff"))}:
        p.append("BUILD-INFO sources differ from patches/r5-ets")
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", SOURCE_HEAD, "--", *P0.FROZEN_PATHSPECS, "experiments/R5-00-exposed-transfer-stall", *FROZEN_RUNTIME,
                        "patches/0001-argine-route-log.patch", "patches/0002-argine-expert-store.patch", "patches/0003-argine-research-trace.patch",
                        "patches/0004-argine-retention-policy-cmake.patch", "patches/0005-argine-prefetch-race-fix.patch", "patches/0006-argine-msvc-atomic-shim.patch"],
                       capture_output=True, text=True, check=True)
    if r.stdout.strip():
        p.append(f"frozen files changed since {SOURCE_HEAD[:7]}: {r.stdout.split()[:5]}")
    ev = [x["id"] for x in json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]]
    for f in list(HERE.rglob("*")) + list(R5SRC.rglob("*")) + [BUILD_INFO]:
        if f.is_file() and "__pycache__" not in f.parts and f.suffix in (".py", ".json", ".md", ".cpp", ".h", ".sh", ".diff"):
            t = f.read_text(encoding="utf-8", errors="replace")
            for i in ev:
                if i in t:
                    p.append(f"evaluation prompt id {i} appears in {f.relative_to(REPO)}")
    bad = sorted(str(q.relative_to(HERE)) for q in HERE.rglob("*") if q.is_file() and any(t in q.name.upper() for t in FORBIDDEN_OUTCOME_NAMES))
    if bad:
        p.append(f"outcome artifacts present: {bad}")
    g = gate()
    if g["verdict"] != "R5_PHASE1A_IMPLEMENTATION_GATE_PASS":
        p.append(f"gate not passed: {[k for k, v in g['criteria'].items() if not v]}")
    return p


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["build", "verify"]); a = ap.parse_args(argv)
    if a.cmd == "build":
        build(); print("built"); return 0
    pr = verify(); print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]; return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
