"""Research 5 Phase 2 -- acquisition readiness: the pre-declared campaign protocol (PROTOCOL_2.json) and its freeze.

    python phase2.py build     write PROTOCOL_2.json and READINESS_2.json (before the evaluation is opened)
    python phase2.py verify    check both, the frozen R5-00 texts they quote, Phase 0, the G1 and G2 seals, and that no
                               evaluation-opening record, campaign record or result exists in this directory

The protocol only makes R5-00 Phase 2 executable: it quotes the frozen contract verbatim (schedule, arms, endpoints,
gates, classification order, blame rules) and fixes the operational choices the contract leaves to the implementation,
BEFORE the evaluation is opened. It opens nothing: the opening is a separate, explicit, one-shot step.
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
SEAL1 = REPO / "experiments" / "R5-01-g1-host-evidence"
SEAL2 = REPO / "experiments" / "R5-02-g2-host-evidence"
P1B = REPO / "experiments" / "R5-02-headroom-gate"
P0_CONTRACT_DIGEST = "9d480ccf1b5109fa9be6cd717261e824876122e1ec504a3ccd85e0e30e619e52"
P0_FREEZE = "21ed630b638ca56a19b6a392a6ce1e47782dcd4351b178d523d5e28a6c7f63ee"
G1_FREEZE = "3827b5a1cf1745869186a79ed50f335f9c6583ab69ca6611dc7f6e633deebce5"
G2_FREEZE = "9d6e6421a9c242dc908d67269c7b950a9b160a62842f2ce9050a4c63878436f2"
P1B_READINESS = "a0f773ab5826e4d705c13e9ea8a48abd2c5e4400957f29e52c4ab56469a3f274"
STATE = "R5_PHASE2_ACQUISITION_READY"
SOURCES = ["campaign.py", "analysis.py", "mechanism.py", "phase2.py", "README.md", "monitor_phase2.ps1",
           "tests/test_mechanism.py", "tests/test_analysis.py", "tests/test_campaign.py", "tests/test_phase2.py", "tests/fixtures.py",
           "../R5-01-ets-implementation/ets_trace.py", "../R5-01-ets-implementation/host_conformance.py",
           "../../tools/r3_04_shutdown.py"]
OUTCOME_NAMES = ["PHASE2_RESULT", "PHASE2_LOG", "ACQUISITION_STOPPED", "IDENTITY_2", "EVALUATION_OPENING", "PHASE2_CAMPAIGN", "RUN_STATE",
                 ".trace"]


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


def _j(name: str) -> Any:
    return json.loads((P0_DIR / name).read_text(encoding="utf-8"))


def frozen_texts() -> Dict[str, Any]:
    dr, en, pr, sc, wl, cm, ins = (_j(n) for n in ("DECISION_RULES.json", "ENDPOINTS.json", "PROTOCOL.json", "SCHEDULE.json", "WORKLOADS.json",
                                                    "COMPARATORS.json", "INSTRUMENTATION.json"))
    return {"question": pr["question"], "hypotheses": pr["hypotheses"], "phase2": pr["phases"]["phase2"], "falsifiers": pr["falsifiers"],
            "schedule": sc, "arms": cm["arms"], "common_environment": cm["common_environment"], "invariants_C_vs_A": cm["invariants_C_vs_A"],
            "endpoints_primary": en["primary"], "endpoints_secondary": en["secondary"], "endpoints_mechanism": en["mechanism"],
            "critical_path_rule": en["critical_path_rule"],
            "gates": {k: dr["gates"][k] for k in ("G3_primary", "G4_mechanism", "G5_no_harm", "G6_consistency", "G7_purity", "INTEGRITY")},
            "classification_order": dr["classification_order"], "label_meaning": dr["label_meaning"], "thresholds": dr["thresholds"],
            "blame_rules": dr["blame_rules"], "anti_rescue": dr["anti_rescue"], "hopi_gate": dr["hopi_gate"], "precision_plan": dr["precision_plan"],
            "launch": wl["launch"], "evaluation_role": wl["evaluation"]["role"], "model_identity": wl["model"]["identity"],
            "host_record_per_launch": ins["host_record_per_launch"], "timing_campaign_trace": ins["timing_campaign_trace"],
            "not_measurable_and_how_handled": ins["not_measurable_and_how_handled"]}


def protocol() -> Dict[str, Any]:
    C = _load("campaign", HERE / "campaign.py")
    sc = _j("SCHEDULE.json")
    slots = C.plan()
    counts = {k: sum(1 for s in slots if s["campaign"] == k) for k in sc["campaign_order"]}
    return {
        "artifact": "R5-03-PHASE2-PROTOCOL", "state": STATE, "eval_state": "LOCKED",
        "scope": "Phase 2 of the frozen R5-00 contract, made executable; no new arm, scheduler variant, workload, budget, parameter or "
                 "threshold; prepared after G1 and G2 PASS and BEFORE the evaluation is opened; opening is a separate explicit step",
        "frozen_texts_quoted": frozen_texts(),
        "counts_confirmed_against_the_contract": {"per_campaign": counts, "counted_total": len(slots), "warmups_minimum": len(sc["campaign_order"]),
                                                  "traced_counted": sum(1 for s in slots if s["traced"]),
                                                  "blocks": {k: len(sc["campaigns"][k]["blocks"]) for k in sc["campaign_order"]}},
        "arm_realisation": {"A": "ARGINE_EXPERT_WAIT=layer, untraced", "B": "layer, ARGINE_RESEARCH_TRACE", "C": "kind, untraced",
                            "C0_I": "layer_touch, ARGINE_RESEARCH_TRACE", "C_I": "kind, ARGINE_RESEARCH_TRACE",
                            "environment": "host_conformance.build_env unchanged (asincrono, direct I/O, ARGINE_PREFETCH=0, budget bytes of the campaign); "
                                           "host_conformance.run_launch unchanged (one fresh server process, 512 greedy tokens, 12 threads, seed 42)",
                            "binary": "the single frozen R5 runtime llama-research-r5-ets-2/llama-server.exe (IMPLEMENTATION_FREEZE)"},
        "operational_choices": [
            "decode ms/token of a launch = server predicted_ms / predicted_n, predicted_n must be 512 (ENDPOINTS primary.definition)",
            "r_b = (mean_A_b - mean_C_b) / mean_A_b; G3: lower limit of the two-sided 95% Student t interval of mean r_b over the 20 "
            "1.5 GiB blocks (df 19) >= 0.02",
            "G5: s_b = -r_b at 4 GiB; upper limit of the two-sided 95% t interval of mean s_b (df 19) <= 0.02",
            "G4 (a): d_b = exposed_wait(C_I) - exposed_wait(C0_I) per mechanism block; upper limit of the two-sided 95% t interval (df 11) "
            "< 0 (strict); the same two-sided convention as the primary and no-harm intervals",
            "G4 (b): mean over the 12 C_I launches of hidden_deferred_bytes_fraction >= 0.25 (a C_I launch without deferred bytes counts 0)",
            "G4 (c): mean[exposed_wait(C0_I) - exposed_wait(C_I)] >= 0.5 x mean[exposed_store(B) - exposed_store(C_I)] if the latter "
            "mean is positive, else (c) holds",
            "G6: count of the 4 evaluation prompts with a positive mean r_b at 1.5 GiB >= 3 (point estimates, no test)",
            "every gate is decided in exact rational arithmetic: measured values exactly as logged (each float bit-exactly), frozen "
            "thresholds as the decimals written in DECISION_RULES (0.02 = 1/50), interval comparisons as (mean - threshold)^2 "
            "against t^2 x variance / n with t(0.975, 19) = 2.093024054408263 (the frozen precision_plan value) and t(0.975, 11) = "
            "2.200985160091639; so no decision depends on the Python build or the platform's floating point; floating-point "
            "intervals are reported for reading only",
            "G7: per campaign, |mean(arm) - mean(ref)| / mean(ref) <= 0.01 for miss bytes per token (end-of-run bytes_read / n_tokens, "
            "reads happen only on misses since no arm prefetches) and evictions per token (evictions / n_tokens), C_I vs B "
            "(mechanism) and C vs A (each timing campaign); budget violations 0 in every counted launch",
            "mechanism metrics per traced launch: mechanism.py (frozen critical-path rule; per-token values over the 511 decode "
            "graphs, as in Phase 1b; strict telemetry checks with the arm's own trace mode)",
            "INTEGRITY: every counted launch passes campaign.launch_problems; per prompt the A and B launches are the token-digest "
            "reference; an A/B disagreement is an integrity failure (R5_INVALID); a C, C_I or C0_I digest differing from a "
            "consistent reference is a correctness failure (R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED)",
            "terminal at once, never rerun: a correctness failure of C, C_I or C0_I (budget violation, pending or orphan reads, "
            "follow-up mismatch, missing touch, drain at thread exit, abort marker in stderr, token digest, deferral in C0_I) and an "
            "integrity failure of A or B (the same counters, deferral, token digest) or of any launch that did not run the scheduled "
            "arm or budget",
            "every other failed or interrupted launch stops the campaign. The launch result (the full record from the runtime) is "
            "saved to the log the moment the server has stopped, before any post-processing; an interruption after that point is "
            "completed by the next run from the saved result and the slot is never rerun",
            "no outcome-dependent repetition: a slot is rerun (once) only if its failed attempt produced NO outcome information "
            "(no saved result with timings, no trace handed over, no request timings printed by the server); otherwise the "
            "failure stands and `finalize` attributes it; warmups (never analysed) are exempt from this condition",
            "every rerun cause must be corroborated by the host itself (the evidence file, hashed and copied into evidence/, "
            "documents, it does not prove): `power` by an unexpected-shutdown event (Kernel-Power 41 or EventLog 6008) since the "
            "failed attempt started, or the driver's own AC-power stop; `windows-update-or-defender` by a Windows Update client "
            "or Defender scan event since the failed attempt started (wevtutil); `disk-full` by the driver's own disk stop or a "
            "disk-full message of the failed launch; `operator-interruption` needs nothing more (the attempt produced no outcome)",
            "a second failure of the same slot aborts the campaign; the label is then fixed by `finalize` (not by `analyze`, "
            "which refuses an unattributed abort): with no corroborated infrastructure cause a C, C_I or C0_I slot -> BLOCKED, "
            "otherwise R5_INVALID",
            "`finalize` attributes an unresolved stop, abort or interruption by blame_rules: without a documented infrastructure "
            "cause a failed C, C_I or C0_I slot is attributed to the intervention (R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED, row 1 "
            "of classification_order: a non-infrastructure failure of C), otherwise the campaign is aborted (R5_INVALID, final); "
            "a wrong arm or budget echo of C, C_I or C0_I is a non-infrastructure failure of the intervention (BLOCKED), of A or B "
            "an integrity failure (R5_INVALID)",
            "token-digest precedence corner: when the A and B launches of a prompt disagree among themselves there is no valid "
            "reference, the C-family launches of that prompt are not judged, and the label is R5_INVALID (both are terminal negatives)",
            "an analysis whose kept evidence does not support it (a kept trace altered or a trace file named by no log record) "
            "writes no result and logs analysis_input_failure; `finalize` then records R5_INVALID (integrity failure of the campaign)",
            "monitoring never affects a launch: heartbeat writes are atomic with retries and their failure is ignored; a stale run "
            "lock (dead pid, or no heartbeat for 2 h) is moved aside atomically, never deleted",
            "warmups: one uncounted launch per campaign with the first arm and prompt of block 1 (identical environment), then 60 s; "
            "additionally one uncounted warmup before the first counted launch after every (re)start, with that launch's arm, "
            "prompt and budget, then 60 s; warmups are recorded and checked for correctness and integrity, never analysed",
            "one run at a time: an exclusive run lock (RUN_STATE.json, created exclusively before any check, holding the pid and a "
            "heartbeat written atomically); `status` reports the open attempt of a live run as running in its phase (pre-launch "
            "checks, launch, post-processing, cooldown), never as failed or interrupted",
            "pause: a file named PAUSE in the output directory stops the run cleanly between launches; not a failure; the block "
            "structure, order and counts are unchanged (no optional stopping: the analysis needs all 196 counted launches)",
            "no endpoint is computed before all 196 counted launches are valid; the analysis runs once and writes PHASE2_RESULT.json",
            "the 36 counted traces are kept gzip'd under attempt-numbered names: the raw hash and intended name are logged when the "
            "runtime hands the trace over, the gzip is written to a .partial name and renamed after hashing, then logged; a "
            "launch_result marker is logged as soon as the server has stopped, before model/store hashing; files of interrupted "
            "attempts are reported, never used; untraced campaigns write no trace; traced and untraced launches are never compared",
            "decode graphs of a traced launch: the one-token graphs after the last multi-token (prefill) graph; if the prompt's "
            "last micro-batch had exactly one token, the first of 512 such graphs is that prefill graph and is excluded (511)",
            "descriptive only, never classifying: per traced launch, read records versus store misses (a read of a resident slice "
            "would make them differ; ENDPOINTS redundant_transfers must be 0, gated in G1); queue depth, transfer-latency "
            "distribution and peak resident bytes are not computed (descriptive in ENDPOINTS, used by no gate); "
            "non_hook_ms_per_token subtracts a per-decode-graph store time (511) from the frozen per-token decode time (512)",
            "every rerun authorization, superseded launch (with its server timings) and interrupted attempt is reported in "
            "PHASE2_RESULT.json, never used",
            "runtime hash before every launch; model and store hashes before the first launch of every (re)start and after every "
            "launch; AC power before and after; host record before and after every launch",
            "evaluation prompts are read only after a valid opening record exists, by id from workloads/v1 (split eval), each "
            "checked against its frozen prompt_object_sha256; prompt text is never printed, logged or written",
        ],
        "preconditions": ["Phase 2 readiness verifies (Phase 0, G1 seal, G2 seal, Phase 1b readiness, sources, protocol)",
                          "G1 sealed R5_G1_HOST_CONFORMANCE_PASS and G2 sealed R5_G2_HEADROOM_GATE_PASS",
                          "R5 runtime SHA-256 = IMPLEMENTATION_FREEZE = host_conformance R5_AUTHORIZED",
                          "model and store SHA-256 = IMPLEMENTATION_FREEZE",
                          "a valid one-shot evaluation-opening record bound to this readiness",
                          "AC power; at least 4 GB free (1 GB before each traced launch)", "no other Phase 2 campaign on the host",
                          "no Phase 2 result committed or present"],
        "monitoring": "campaign.py status [--json] and monitor_phase2.ps1 report counts, the running slot and its phase (launch, "
                      "post-processing, cooldown), interrupted attempts, failures, pause and stop state; never a timing, metric or label",
        "outcome_leakage_limits": "the driver and the monitor never print or summarise a timing, metric or label before the one-shot "
                                  "analysis. The acquisition log, the server logs (llama-server prints each request's timings) and the "
                                  "kept traces are on the operator's disk and can be read; software cannot prevent that. What is bounded: "
                                  "no stopping rule depends on outcomes (counts are constants; nothing is analysed before all 196 counted "
                                  "launches; abandoning yields no positive label); a slot can be repeated at most once and only for a "
                                  "listed, corroborated infrastructure cause; an operator interruption is refused once a result exists; "
                                  "every rerun and every superseded launch, with its timings, is reported in the result",
        "estimates": {
            "disk": "36 kept gzip traces of about 47-49 MB each (about 1.8 GB) + one raw trace at a time (about 0.24 GB) + logs (about "
                    "20 MB): about 2.1 GB; at least 4 GB free required",
            "duration": "ESTIMATE from the Phase 1b host log (traced launch about 155 s plus about 3-4 min of post-processing and hashing): "
                        "mechanism 37-38 traced launches x 5.5-7 min = about 3.5-4.5 h; timing 162 untraced launches x 3.5-5 min (launch "
                        "about 2.5 min, model/store hashing, 10 s cooldown) = about 9.5-13.5 h; total about 13-18 h, possibly over "
                        "several sessions (PAUSE between launches)",
        },
        "disclosures": {"superseded_phase1b_assertion": "the frozen Phase 1b test test_phase1b.py::test_no_phase2_artifact asserts (1) no "
                                                        "evaluation-opening file exists and (2) no experiments/R5-03* directory exists. (1) "
                                                        "still holds. (2) recorded the pre-G2 state 'Phase 2 not prepared' and fails by design "
                                                        "now that G2 is sealed PASS and Phase 2 is prepared here; the test is hashed into the "
                                                        "Phase 1b readiness (a0f773ab...) bound by the G2 seal, so it is not edited",
                        "deviation_from_blame_rules": "STRICTER than the frozen blame_rules ('a slot failed for an infrastructure "
                                                      "cause may be rerun once'): a slot whose failed attempt produced outcome "
                                                      "information is never rerun, even for a genuine, corroborated infrastructure "
                                                      "cause; this can only move the result toward a negative or invalid label, never "
                                                      "toward an improvement, and prevents outcome-dependent repetition",
                        "torn_log_line": "a power loss during an append can leave a torn last log line; `run` moves it to "
                                         "PHASE2_LOG.torn.N.txt and logs the repair; a torn launch or result record leaves its attempt "
                                         "open and it is then treated by the rules above",
                        "stale_lock_race": "two runs started at the same moment over a stale lock have a very narrow race; start one run",
                        "unrecoverable_window": "a crash in the few seconds between the server printing its request timings and the "
                                                "saved launch result (server shutdown), or between the trace hand-over and the saved "
                                                "result, cannot be completed and, because an outcome may have been visible, cannot be "
                                                "rerun: the slot's failure stands and `finalize` attributes it (C-family: BLOCKED; A/B: "
                                                "R5_INVALID); this trades a small risk of losing the campaign for the absence of any "
                                                "outcome-dependent repetition",
                        "host_corroboration_untested_here": "the event-log, AC-power and boot queries run only on Windows; the tests exercise "
                                                            "them through mocks",
                        "resume_warmup": "a resume warmup uses the next slot's arm, so after a resume that slot's predecessor launch "
                                         "differs from the schedule's (for example C then C instead of A then C); uncounted, the same for "
                                         "every arm, pre-declared",
                        "evidence_files": "the opening record and the campaign marker are host files; their uniqueness rests on committing "
                                          "them with the sealed Phase 2 evidence",
                        "v5_comparator": "G1 baseline fidelity used the reconstructed llama-research-v5 comparator, not the lost original; "
                                         "Phase 2 does not use v5 (all arms run the frozen R5 binary)",
                        "G2": "H = 0.0844 of decode time is an optimistic opening bound, never an expected or achieved gain",
                        "tracing": "B, C0_I and C_I are traced; tracing perturbs timing (EXP-024); traced arms are compared only with each other"},
        "outputs": ["IDENTITY_2.json", "PHASE2_LOG.jsonl", "launches/*.log", "traces/*.trace.gz (36 counted traced launches)",
                    "PHASE2_RESULT.json (once)", "ACQUISITION_STOPPED.json (only if stopped)"],
    }


def readiness() -> Dict[str, Any]:
    rec = {"artifact": "R5-03-PHASE2-READINESS", "state": STATE, "eval_state": "LOCKED", "evaluation_opened": False,
           "phase0": {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE},
           "g1_freeze_sha256": G1_FREEZE, "g2_freeze_sha256": G2_FREEZE, "phase1b_readiness_sha256": P1B_READINESS,
           "protocol_lf_sha256": lf_sha(HERE / "PROTOCOL_2.json"),
           "sources_lf_sha256": {s: lf_sha((HERE / s).resolve()) for s in SOURCES}}
    rec["readiness_sha256"] = sha_bytes(canon(rec))
    return rec


def build() -> str:
    (HERE / "PROTOCOL_2.json").write_text(dumps(protocol()), encoding="utf-8", newline="\n")
    r = readiness()
    (HERE / "READINESS_2.json").write_text(dumps(r), encoding="utf-8", newline="\n")
    return r["readiness_sha256"]


def outcome_files() -> List[str]:
    return sorted(str(q.relative_to(HERE)) for q in HERE.rglob("*") if q.is_file() and "__pycache__" not in q.parts
                  and any(t in q.name for t in OUTCOME_NAMES))


def verify() -> List[str]:
    p: List[str] = []
    if json.loads((HERE / "PROTOCOL_2.json").read_text(encoding="utf-8")) != json.loads(dumps(protocol())):
        p.append("PROTOCOL_2.json differs from its definition (or a quoted frozen R5-00 text changed)")
    r = json.loads((HERE / "READINESS_2.json").read_text(encoding="utf-8"))
    if r["readiness_sha256"] != sha_bytes(canon({k: v for k, v in r.items() if k != "readiness_sha256"})):
        p.append("READINESS_2 self-hash")
    if r != json.loads(dumps(readiness())):
        p.append("READINESS_2 differs from the sources, protocol or upstream seals")
    p0 = _load("phase0", P0_DIR / "phase0.py")
    p += [f"phase0: {x}" for x in p0.verify()]
    fz = json.loads((P0_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if (fz["contract_digest"], fz["freeze_sha256"]) != (P0_CONTRACT_DIGEST, P0_FREEZE):
        p.append("Phase 0 digests")
    g1 = _load("g1_seal", SEAL1 / "g1_seal.py")
    p += [f"g1_seal: {x}" for x in g1.verify()]
    g2 = _load("g2_seal", SEAL2 / "g2_seal.py")
    p += [f"g2_seal: {x}" for x in g2.verify()]
    if json.loads((SEAL1 / "FREEZE.json").read_text(encoding="utf-8"))["freeze_sha256"] != G1_FREEZE:
        p.append("G1 seal digest")
    if json.loads((SEAL2 / "FREEZE.json").read_text(encoding="utf-8"))["freeze_sha256"] != G2_FREEZE:
        p.append("G2 seal digest")
    if json.loads((P1B / "READINESS.json").read_text(encoding="utf-8"))["readiness_sha256"] != P1B_READINESS:
        p.append("Phase 1b readiness digest")
    if json.loads((SEAL2 / "G2_SEAL.json").read_text(encoding="utf-8"))["verdict"] != "R5_G2_HEADROOM_GATE_PASS":
        p.append("G2 is not PASS")
    if outcome_files():
        p.append(f"Phase 2 outcome or opening artifacts present before the opening: {outcome_files()}")
    if any((HERE / "workloads").glob("*")) or any(q.name in ("coding.json", "natural-language.json", "reasoning-math.json", "structured-json.json")
                                                 for q in HERE.rglob("*.json")):
        p.append("a workload corpus file is present in the Phase 2 directory")
    return p


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "verify"])
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print("readiness_sha256", build()); return 0
    pr = verify()
    print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]
    return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
