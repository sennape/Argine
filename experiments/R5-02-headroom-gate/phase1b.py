"""Research 5 Phase 1b -- acquisition readiness: the pre-declared protocol (PROTOCOL_1B.json) and its freeze.

    python phase1b.py build     write PROTOCOL_1B.json and READINESS.json (before any Phase 1b data exist)
    python phase1b.py verify    check both, the frozen R5-00 texts they quote, the sealed G1 and the absence of results

The protocol only makes R5-00's one-shot gate G2 executable: it quotes the frozen rule texts verbatim and fixes the
operational choices the contract leaves to the implementation, BEFORE any Phase 1b launch. Nothing here opens Phase 2.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P0_DIR = REPO / "experiments" / "R5-00-exposed-transfer-stall"
SEAL = REPO / "experiments" / "R5-01-g1-host-evidence"
P0_CONTRACT_DIGEST = "9d480ccf1b5109fa9be6cd717261e824876122e1ec504a3ccd85e0e30e619e52"
P0_FREEZE = "21ed630b638ca56a19b6a392a6ce1e47782dcd4351b178d523d5e28a6c7f63ee"
STATE = "R5_PHASE1B_ACQUISITION_READY"
SOURCES = ["headroom.py", "acquire.py", "phase1b.py", "README.md", "tests/test_headroom.py", "tests/test_acquire.py", "tests/test_phase1b.py",
           "../R5-01-ets-implementation/ets_trace.py", "../R5-01-ets-implementation/host_conformance.py"]
OUTCOME_NAMES = ["G2_RESULT", "PHASE1B_LOG", "ACQUISITION_STOPPED", "IDENTITY_1B", ".trace"]


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def lf_sha(p: Path) -> str: return sha_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)            # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                           # type: ignore[union-attr]
    return mod


def frozen_texts() -> Dict[str, Any]:
    dr = json.loads((P0_DIR / "DECISION_RULES.json").read_text(encoding="utf-8"))
    en = json.loads((P0_DIR / "ENDPOINTS.json").read_text(encoding="utf-8"))
    pr = json.loads((P0_DIR / "PROTOCOL.json").read_text(encoding="utf-8"))
    sc = json.loads((P0_DIR / "SCHEDULE.json").read_text(encoding="utf-8"))
    wl = json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))
    return {"G2_headroom": dr["gates"]["G2_headroom"], "headroom_gate": dr["thresholds"]["headroom_gate"],
            "headroom_bound_H": en["headroom_bound_H"], "critical_path_rule": en["critical_path_rule"],
            "phase1b": pr["phases"]["phase1b"], "IMPLEMENTATION_FREEZE": dr["gates"]["IMPLEMENTATION_FREEZE"],
            "threshold_justification_H": dr["threshold_justification"]["H_gate_4pct"],
            "warmup_convention": sc["warmup"], "cooldown_between_launches_s": sc["cooldown_between_launches_s"],
            "host_conditions": sc["host_conditions"], "launch": wl["launch"], "model_identity": wl["model"]["identity"],
            "anti_rescue": dr["anti_rescue"]}


def protocol() -> Dict[str, Any]:
    A = _load("acquire", HERE / "acquire.py")
    HR = _load("headroom", HERE / "headroom.py")
    return {
        "artifact": "R5-02-PHASE1B-PROTOCOL",
        "state": STATE,
        "question": "Does the frozen R5 runtime's baseline (arm B: layer wait, traced) leave enough expert-transfer wait on the decode "
                    "critical path, hideable behind the compute that follows the first expert node of each layer, to justify opening "
                    "Phase 2? Operationally: is the optimistic bound H >= 4% of decode time (G2)?",
        "scope": "development prompts only; one-shot; no scheduler variant; no Phase 2 artifact; H is never an achievable gain",
        "frozen_texts_quoted": frozen_texts(),
        "estimator": {
            "implementation": "experiments/R5-02-headroom-gate/headroom.py (launch_h, decide)",
            "operational_choices": [
                "critical-path wait interval of the first node = [wait_start, wait_end] of the thread whose wait ENDS LAST (ties -> the "
                "longer wait): the node cannot exit before that thread, so only that interval is on the critical path. first_node_wait "
                "= the maximum over threads of wait_end - wait_start; invariant H_layer <= first_node_wait (asserted)",
                "c_first = latest completion of a read of the first node's kind issued at the first node (or the critical wait start if "
                "there is none); c_all = latest completion of a read issued at the first node that is not flagged SYNCHRONOUS (4), "
                "FALLBACK (8) or SYNC_IN_ISSUE (16); T_tail = max(0, min(c_all, wait_end) - max(c_first, wait_start))",
                "W = max(0, min hook_in of the layer's last expert node - max hook_out of the first node): the compute window in which "
                "the deferred reads could at best be hidden",
                "H_layer = min(T_tail, W); exactly one value per decode layer event, so overlapping or simultaneous reads are never "
                "summed (read/read overlap is not read/compute overlap); the union of all threads' wait windows is never used",
                "reads issued at the first node = I records of the layer whose issue time lies in the first node's window "
                "[min hook_in, max hook_out]; every read must be attributable to exactly one node window, else the trace is invalid",
                "reads flagged 4, 8 or 16 never extend the tail (paid synchronously, never hideable); a bounded completion (flag 2, "
                "recorded time = an upper bound) is taken at its optimistic end on each side: the recorded time for c_all and the "
                "issue time for c_first, so a late poll never shrinks H",
                "thread identity: every node has exactly 12 T records with 12 distinct thread ids, the same id set in every node; every "
                "I record's thread id is in that set (arx_thread_id is shared by a thread's T and I records; ids need not start at 0)",
                "decode tokens = decode graphs of the trace (router records with n_tokens = 1) and must equal predicted_n - 1 = 511",
                "H = (sum over the 8 launches of sum H_layer, in ms) / (sum over the 8 launches of predicted_ms): the share of the "
                "measured decode time (WORKLOADS decode_metric_source) that is at best hideable",
                "G2: H >= 1/25 in exact rational arithmetic (inclusive at exactly 4%)",
                "every per-launch H fraction is reported with the pooled H; only the pooled H decides",
                "telemetry is validated (pass/fail only, no H) right after every launch, warmup included, so an invalid trace stops the "
                "acquisition at once instead of after 9 launches",
            ],
            "independent_review": "an independent reviewer challenged bound validity, double counting, causality, tracing bias, critical "
                                  "path, endpoint consistency and outcome dependence before acquisition; the 4 material findings "
                                  "(critical-wait thread choice, thread-id rule, readiness binding, rerun policy) were resolved; a second "
                                  "review found 2 more (flag-2 gate reads shrinking H; a seen-then-interrupted slot being rerun and "
                                  "overwritten), resolved by the issue-time rule above and the rerun rules below; the second reviewer then ACCEPTED, and its "
                                  "one residual minor gap (raw trace on disk before it is logged) was closed by trace_received and the "
                                  "traces/ accounting",
            "differs_from_phase1a_draft": "experiments/R5-01-ets-implementation/ets_trace.py metrics_from_log (never used to compute an H) "
                                          "clips T_tail to the union of all threads' wait windows and lets synchronous reads extend the "
                                          "tail; both can exceed the critical-path wait. headroom.py is the Phase 1b estimator; the draft "
                                          "is not used for G2.",
            "telemetry_fail_closed": "unparseable records, wrong schema or arm, missing thread records, incoherent records, failed or "
                                     "unattributable reads, missing first node, single-node events, decode-graph mismatch, truncated or "
                                     "undecodable files: no H is computed",
        },
        "schedule": {
            "arm": "B = ARGINE_EXPERT_WAIT=layer (explicit), ARGINE_RESEARCH_TRACE on, ARGINE_PREFETCH=0, asincrono, direct I/O",
            "budget": "1.5 GiB (1610612736 bytes)",
            "warmup": f"1 uncounted B launch with {A.WARMUP_PROMPT} (SCHEDULE warmup convention), structure recorded, trace deleted, never used; "
                      f"then {A.WARMUP_COOLDOWN_S} s cooldown",
            "counted_order": A.ORDER,
            "counted_launches": len(A.ORDER),
            "cooldown_s": A.COOLDOWN_S,
            "launch": "one fresh server process per launch; 512 greedy tokens (ignore_eos, temperature 0, top_k 1, seed 42), 12 threads, "
                      "context 4096 (host_conformance.py run_launch, unchanged)",
            "per_launch_checks": "model and store SHA-256 against the IMPLEMENTATION_FREEZE before the first launch of every (re)start and "
                                 "after every launch; AC power before every launch and in the host record after it (unknown = stop); "
                                 "host record before and after; telemetry validation; launch validity (512 tokens, graceful shutdown, one counter record, explicit layer arm, prefetch 0, budget, "
                                 "0 violations / pending / drain / deferred reads, trace structure complete, 511 decode graphs)",
            "invalid_launch": "the acquisition stops (no H). R5-00 blame_rules: a failed B launch is attributed to the campaign; one "
                              "rerun of that slot only with --rerun-infrastructure CAUSE (power, windows-update-or-defender, disk-full, "
                              "operator-interruption) --evidence FILE (hashed into the log); a second failure of the same slot aborts "
                              "the campaign and G2 stays undecided. A counted trace is kept under an attempt-numbered name and its "
                              "hash logged before any post-launch work; an attempt interrupted after that point cannot be rerun for "
                              "operator-interruption; every superseded kept trace is reported in G2_RESULT (never deciding), and a "
                              "missing or altered one blocks the gate. A trace_received record is logged as soon as the runtime's raw "
                              "trace is handed over (interruptions from then on count as after the trace); a raw trace left in "
                              "traces/ is analyzed and reported (never deciding), and any other unaccounted file blocks the gate. An attempt without a launch record counts as a failure. Every "
                              "(re)start begins with a fresh uncounted warmup. A campaign marker in %LOCALAPPDATA%\\Argine\\r5 "
                              "refuses a second output directory.",
        },
        "preconditions": ["R5-00 phase0 verify passes", "G1 sealed (g1_seal verify) with R5_G1_HOST_CONFORMANCE_PASS",
                          "no Phase 1b result committed or present in the output directory (one-shot)",
                          "R5 runtime SHA-256 = IMPLEMENTATION_FREEZE = host_conformance R5_AUTHORIZED",
                          "development prompts only (evaluation ids are refused)", "at least 2 GB free in the output directory",
                          "Phase 1b readiness verifies; its digest and the estimator, driver and protocol hashes are recorded in "
                          "IDENTITY_1B.json and re-checked before H is computed",
                          "no other Phase 1b campaign marker on the host; an existing output directory must have the same identity"],
        "decision": {"threshold": float(HR.HEADROOM_GATE), "inclusive": True, "pass_label": HR.LABEL_PASS, "fail_label": HR.LABEL_FAIL,
                     "pass_means": "Phase 2 MAY be opened by its own, separate, later step; nothing is opened here",
                     "fail_means": "R5_NO_MEANINGFUL_OVERLAP_HEADROOM: the direction is closed before evaluation; the evaluation is never opened",
                     "never": ["recompute H with another definition", "change the threshold", "add launches or prompts", "describe H as a gain"]},
        "outputs": {"in_output_directory": ["IDENTITY_1B.json", "PHASE1B_LOG.jsonl (one record per launch)", "launches/*.log",
                                            "traces/*.trace.gz (the 8 counted traces, kept; SHA-256 in the log)", "G2_RESULT.json (once)",
                                            "ACQUISITION_STOPPED.json (only if stopped)"],
                    "disk_estimate": "raw trace about 0.24 GB per launch (G1 traced B launch: 236 MB), one at a time; kept gzip traces "
                                     "about 35-60 MB each (ratio about 0.15-0.25 on real-format traces), about 0.3-0.5 GB for 8; logs about 1 MB. "
                                     "2 GB free required.",
                    "duration_estimate": "ESTIMATE: about 4-5 min per launch (G1 averaged 2.5 min per launch; plus tracing, model/store hashing "
                                         "before every launch, trace structure and gzip, cooldown) x 9 launches, plus about 5 min for the "
                                         "one-shot analysis: roughly 45-75 min in total"},
    }


def readiness() -> Dict[str, Any]:
    g1 = json.loads((SEAL / "FREEZE.json").read_text(encoding="utf-8"))
    rec = {"artifact": "R5-02-PHASE1B-READINESS", "state": STATE, "eval_state": "LOCKED", "g2_computed": False,
           "phase0": {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE},
           "g1_freeze_sha256": g1["freeze_sha256"],
           "protocol_lf_sha256": lf_sha(HERE / "PROTOCOL_1B.json"),
           "sources_lf_sha256": {s: lf_sha((HERE / s).resolve()) for s in SOURCES}}
    rec["readiness_sha256"] = sha_bytes(canon(rec))
    return rec


def build() -> str:
    (HERE / "PROTOCOL_1B.json").write_text(dumps(protocol()), encoding="utf-8", newline="\n")
    r = readiness()
    (HERE / "READINESS.json").write_text(dumps(r), encoding="utf-8", newline="\n")
    return r["readiness_sha256"]


def outcome_files() -> List[str]:
    return sorted(str(q.relative_to(HERE)) for q in HERE.rglob("*") if q.is_file() and "__pycache__" not in q.parts
                  and any(t in q.name for t in OUTCOME_NAMES))


def verify() -> List[str]:
    p: List[str] = []
    if json.loads((HERE / "PROTOCOL_1B.json").read_text(encoding="utf-8")) != json.loads(dumps(protocol())):
        p.append("PROTOCOL_1B.json differs from its definition (or a quoted frozen R5-00 text changed)")
    r = json.loads((HERE / "READINESS.json").read_text(encoding="utf-8"))
    if r["readiness_sha256"] != sha_bytes(canon({k: v for k, v in r.items() if k != "readiness_sha256"})):
        p.append("READINESS self-hash")
    if r != json.loads(dumps(readiness())):
        p.append("READINESS differs from the sources, protocol or G1 seal")
    p0 = _load("phase0", P0_DIR / "phase0.py")
    p += [f"phase0: {x}" for x in p0.verify()]
    fz = json.loads((P0_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if (fz["contract_digest"], fz["freeze_sha256"]) != (P0_CONTRACT_DIGEST, P0_FREEZE):
        p.append("Phase 0 digests")
    g1 = _load("g1_seal", SEAL / "g1_seal.py")
    p += [f"g1_seal: {x}" for x in g1.verify()]
    if outcome_files():
        p.append(f"Phase 1b outcome artifacts present before acquisition: {outcome_files()}")
    ev = [x["id"] for x in json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]]
    for q in HERE.rglob("*"):
        if q.is_file() and q.suffix in (".py", ".json", ".md") and "__pycache__" not in q.parts:
            if any(i in q.read_text(encoding="utf-8") for i in ev):
                p.append(f"evaluation prompt id in {q.relative_to(HERE)}")
    return p


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["build", "verify"]); a = ap.parse_args(argv)
    if a.cmd == "build":
        print("readiness_sha256", build()); return 0
    pr = verify(); print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]
    return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
