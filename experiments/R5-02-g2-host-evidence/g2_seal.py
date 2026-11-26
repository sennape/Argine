"""Research 5 Phase 1b -- sealing of the canonical G2 headroom-gate evidence.

    python g2_seal.py seal --host-dir DIR --marker FILE --profile-prefix PREFIX --rederived FILE --clean-room FILE
    python g2_seal.py verify [--host-dir DIR] [--rederive]

`seal` copies the canonical Phase 1b output directory's records and launch logs into host/ with ONE deterministic
transform (the user-profile prefix becomes %USERPROFILE%; CRLF becomes LF), records the size and raw SHA-256 of every
original file (MANIFEST.json; the 8 kept gzip traces are hashed, never committed), stores the independent
re-derivations of H (REDERIVATION.json), the verified verdict (G2_SEAL.json) and FREEZE.json.

`verify` re-checks every hash, the binding of the acquisition to the committed Phase 1b readiness, the schedule, the
integrity of the acquisition log, and recomputes H in exact rational arithmetic from the per-launch integer sums of
H_layer and the logged server timings. With --host-dir it re-checks the raw host files; with --rederive it also
recomputes every per-launch H_layer sum from the kept traces with the frozen estimator (minutes).

G2 measures an optimistic overlap OPPORTUNITY (an upper bound on hideable expert-transfer wait). It is never an
achieved or achievable runtime gain. Nothing here opens the evaluation or Phase 2.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P0_DIR = REPO / "experiments" / "R5-00-exposed-transfer-stall"
G1_DIR = REPO / "experiments" / "R5-01-g1-host-evidence"
P1B = REPO / "experiments" / "R5-02-headroom-gate"
HOST = HERE / "host"
TOKEN = "%USERPROFILE%"
P0_CONTRACT_DIGEST = "9d480ccf1b5109fa9be6cd717261e824876122e1ec504a3ccd85e0e30e619e52"
P0_FREEZE = "21ed630b638ca56a19b6a392a6ce1e47782dcd4351b178d523d5e28a6c7f63ee"
G1_FREEZE = "3827b5a1cf1745869186a79ed50f335f9c6583ab69ca6611dc7f6e633deebce5"
P1B_READINESS = "a0f773ab5826e4d705c13e9ea8a48abd2c5e4400957f29e52c4ab56469a3f274"
READINESS_COMMIT = "2f996ff7923cbc46d153c44731f3efb637e0b663"
VERDICT = "R5_G2_HEADROOM_GATE_PASS"
THRESHOLD = Fraction(1, 25)
COMMITTED = ("G2_RESULT.json", "IDENTITY_1B.json", "PHASE1B_LOG.jsonl")
MARKER = "campaign-marker/PHASE1B_CAMPAIGN.json"
ORDER = ["code-dev-1", "nl-dev-1", "math-dev-1", "json-dev-1", "json-dev-1", "math-dev-1", "nl-dev-1", "code-dev-1"]


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()
def lf(b: bytes) -> bytes: return b.replace(b"\r\n", b"\n")
def lf_sha(p: Path) -> str: return sha_bytes(lf(p.read_bytes()))
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)            # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                           # type: ignore[union-attr]
    return mod


def sanitize(b: bytes, prefix: str) -> bytes:
    p = prefix.rstrip("\\/")
    for v in (p.replace("\\", "\\\\"), p, p.replace("\\", "/")):
        b = b.replace(v.encode("utf-8"), TOKEN.encode("utf-8"))
    return b


def evidence_files(host_dir: Path) -> List[Path]:
    return sorted(p for p in host_dir.rglob("*") if p.is_file())


def committed_rel(rel: str) -> bool:
    return rel in COMMITTED or (rel.startswith("launches/") and rel.endswith(".log"))


def records(host: Path) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in (host / "PHASE1B_LOG.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------------------------------------ the checks
def exact_h(rederived: List[Dict[str, Any]], recs: List[Dict[str, Any]]) -> Fraction:
    """H = (sum of the 8 launches' integer H_layer sums, ns -> ms) / (sum of their server predicted_ms), exactly."""
    t = {r["seq"]: r["server_timings"] for r in recs if r.get("type") == "launch" and r.get("counted")}
    num = Fraction(sum(x["h_ns"] for x in rederived), 1_000_000)
    den = sum((Fraction(t[x["seq"]]["predicted_ms"]) for x in rederived), Fraction(0))
    return num / den


def acquisition_checks(host: Path) -> Dict[str, bool]:
    """Schedule, warmup exclusion, slot validity, attempts, identity binding and log integrity (no H is computed)."""
    A = _load("acquire", P1B / "acquire.py")
    recs = records(host)
    ident = json.loads((host / "IDENTITY_1B.json").read_text(encoding="utf-8"))
    res = json.loads((host / "G2_RESULT.json").read_text(encoding="utf-8"))
    launches = [r for r in recs if r.get("type") == "launch"]
    counted = [r for r in launches if r.get("counted")]
    warm = [r for r in launches if not r.get("counted")]
    imf = json.loads((G1_DIR / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))
    rd = json.loads((P1B / "READINESS.json").read_text(encoding="utf-8"))
    types = [r.get("type") for r in recs]
    c: Dict[str, bool] = {}
    c["schedule_8_counted_in_predeclared_order"] = [r["seq"] for r in counted] == list(range(1, 9)) and [r["prompt"] for r in counted] == ORDER
    c["warmup_one_uncounted_excluded"] = len(warm) == 1 and warm[0]["seq"] == 0 and warm[0]["prompt"] == A.WARMUP_PROMPT and \
        not (warm[0].get("trace") or {}).get("kept") and all(x["seq"] != 0 for x in res["launches"])
    c["all_slots_arm_B_budget_1_5_traced"] = all(r["arm"] == "A" and r["traced"] and r["budget"] == "1.5" and r["runtime"] == "r5" for r in launches) \
        and all((r.get("r5_counters") or {}).get("expert_wait") == "layer" for r in launches)
    c["every_launch_valid_by_the_frozen_driver"] = all(not A.launch_problems(r) for r in launches)
    c["one_attempt_per_slot_no_reruns"] = all(r["attempt"] == 1 for r in launches) and "rerun_authorized" not in types and \
        sum(1 for r in recs if r.get("type") == "attempt_start") == 9
    c["no_superseded_or_unaccounted_traces"] = res["superseded_attempts"] == [] and \
        sorted(r["kept"] for r in recs if r.get("type") == "trace_kept") == sorted(r["trace"]["kept"] for r in counted)
    c["log_order_attempt_received_kept_launch"] = types == ["attempt_start", "launch"] + ["attempt_start", "trace_received", "trace_kept", "launch"] * 8
    c["trace_hashes_logged_before_launch_record_agree"] = all(
        k["gz_sha256"] == r["trace"]["gz_sha256"] and k["sha256"] == r["trace"]["sha256"]
        for k, r in zip([x for x in recs if x.get("type") == "trace_kept"], counted))
    c["telemetry_valid_all_launches"] = all((r["trace"].get("telemetry") or {}).get("valid") and r["trace"]["telemetry"]["decode_graphs"] == 511
                                            for r in launches)
    c["runtime_identity_frozen"] = ident["runtime_sha256"] == imf["binaries_sha256"]["llama-server.exe"] == \
        "da6e7183e44f6f69d58156cf683f5a929f16fc74bba442fdea39f139e4007493"
    c["model_store_identity_after_every_launch"] = all(r["model_store_after"].get("ok") and r["model_store_after"]["model_sha256"] == imf["model"]["sha256"]
                                                       and r["model_store_after"]["store_sha256"] == imf["store"]["sha256"] for r in launches)
    c["ac_power_before_and_after_every_launch"] = all(r["host_before"]["ac_power"] is True and r["host_after"]["ac_power"] is True for r in launches)
    c["readiness_binding"] = ident["readiness_sha256"] == rd["readiness_sha256"] == P1B_READINESS and ident["g1_freeze_sha256"] == G1_FREEZE \
        and ident["headroom_py_lf_sha256"] == lf_sha(P1B / "headroom.py") and ident["acquire_py_lf_sha256"] == lf_sha(P1B / "acquire.py") \
        and ident["protocol_lf_sha256"] == lf_sha(P1B / "PROTOCOL_1B.json") and res["identity"] == ident
    c["token_digests_one_per_prompt"] = all(len({r["token_digest"] for r in launches if r["prompt"] == p}) == 1 for p in set(ORDER))
    c["host_result_one_shot"] = res.get("computed_once") is True and res["gate_G2"] == "PASS" and res["label"] == VERDICT
    return c


# ------------------------------------------------------------------------------------------------ seal
def seal(host_dir: Path, marker: Path, profile_prefix: str, rederived: Path, clean_room: Path) -> Dict[str, Any]:
    user = Path(profile_prefix.replace("\\", "/")).name
    if HOST.exists():
        shutil.rmtree(HOST)
    manifest = []
    sources = [(p, p.relative_to(host_dir).as_posix()) for p in evidence_files(host_dir)] + [(marker, MARKER)]
    for p, rel in sources:
        raw = p.read_bytes()
        e = {"path": rel, "bytes": len(raw), "raw_sha256": sha_bytes(raw), "committed": committed_rel(rel) or rel == MARKER}
        if e["committed"]:
            s = sanitize(raw, profile_prefix)
            if user and user.encode() in s:
                raise SystemExit(f"personal path left in {rel} after sanitizing")
            dst = HOST / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(lf(s))
            e.update(committed_path=f"host/{rel}", committed_lf_sha256=sha_bytes(lf(s)), sanitized=s != raw)
        manifest.append(e)
    recs = records(HOST)
    res = json.loads((HOST / "G2_RESULT.json").read_text(encoding="utf-8"))
    fr = json.loads(rederived.read_text(encoding="utf-8"))
    cr = json.loads(clean_room.read_text(encoding="utf-8"))
    counted = {r["seq"]: r for r in recs if r.get("type") == "launch" and r.get("counted")}
    per = []
    for x in fr["launches"]:
        r = counted[x["seq"]]
        per.append({"seq": x["seq"], "prompt": r["prompt"], "trace_gz": r["trace"]["kept"], "gz_sha256": x["gz_sha256"], "raw_sha256": x["raw_sha256"],
                    "raw_bytes": x["raw_bytes"], "h_ns": x["h_ns"], "decode_graphs": x["decode_tokens"], "events": x["events"],
                    "events_positive": x["events_positive"], "first_node_wait_ns": x["first_wait_ns"], "tail_ns": x["tail_ns"], "gap_ns": x["gap_ns"],
                    "first_node_reads": x["first_node_reads"], "not_overlapped_reads": x["not_overlapped_reads"],
                    "bounded_completion_reads": x["bounded_completion_reads"], "predicted_ms": r["server_timings"]["predicted_ms"],
                    "predicted_n": r["server_timings"]["predicted_n"]})
    clean_h = {int(k): v for k, v in _clean_per_launch(cr).items()}
    H = exact_h(per, recs)
    write("REDERIVATION.json", {
        "artifact": "R5-02-G2-REDERIVATION",
        "frozen_estimator": {"code": "experiments/R5-02-headroom-gate/headroom.py (launch_h, decide) at the readiness commit " + READINESS_COMMIT,
                             "input": "the 8 kept gzip traces of the canonical host directory (hashes re-checked against the log)",
                             "decision": fr["decision"]},
        "clean_room": {"method": "a separate agent wrote its own estimator from ENDPOINTS headroom_bound_H and PROTOCOL_1B operational_choices only "
                                 "(it did not open headroom.py, acquire.py, their tests or G2_RESULT.json) and ran it on the same 8 traces",
                       "per_launch_h_ns": clean_h, "H_exact": f"{cr['H_numerator']}/{cr['H_denominator']}", "all_telemetry_checks_pass": cr["telemetry_all_valid"] is True,
                       "code": "independent/clean_room_h.py",
                       "ambiguities_resolved": [
                           "a graph counts as decode only if its router records have n_tokens = 1 (prefill graphs carry one n_tokens = 1 "
                           "record at the output layer); 511 decode graphs either way and the same H",
                           "an event with no eligible deferred read, or no critical wait, contributes H_layer = 0",
                           "c_first includes first-kind reads of any flag (flag 2 at its issue time), as PROTOCOL_1B states"]},
        "per_launch": per,
        "agreement": all(clean_h.get(x["seq"]) == x["h_ns"] for x in per),
        "sum_h_ns": sum(x["h_ns"] for x in per),
        "H_numerator_ms": str(Fraction(sum(x["h_ns"] for x in per), 1_000_000)),
        "sum_predicted_ms": str(sum((Fraction(x["predicted_ms"]) for x in per), Fraction(0))),
        "H_exact": f"{H.numerator}/{H.denominator}", "H": float(H),
    })
    acq = acquisition_checks(HOST)
    write("MANIFEST.json", {
        "artifact": "R5-02-G2-EVIDENCE-MANIFEST",
        "canonical_host_directory": f"{TOKEN}\\AppData\\Local\\Argine\\r5\\phase1b-headroom (on the Windows host; kept, not deleted)",
        "campaign_marker": f"{TOKEN}\\AppData\\Local\\Argine\\r5\\PHASE1B_CAMPAIGN.json (committed as {MARKER})",
        "files": manifest, "n_files": len(manifest), "bytes_total": sum(e["bytes"] for e in manifest),
        "committed_transform": f"user-profile prefix -> {TOKEN} (plain and JSON-escaped), CRLF -> LF; nothing else",
        "not_committed": "traces/*.trace.gz (8 kept gzip traces, about 47-49 MB each, 386 MB in all): hashed here, kept on the host; "
                         "their uncompressed SHA-256 and size are in REDERIVATION.json and agree with the acquisition log",
    })
    hx = Fraction(*map(int, res["H_exact"].split("/")))
    write("G2_SEAL.json", {
        "artifact": "R5-02-G2-SEAL",
        "verdict": VERDICT, "gate": "G2_headroom (R5-00 DECISION_RULES gates.G2_headroom)",
        "H": float(H), "H_exact": f"{H.numerator}/{H.denominator}", "H_percent_of_decode_time": float(H * 100),
        "threshold": float(THRESHOLD), "threshold_exact": "1/25", "inclusive": True, "H_over_threshold": float(H / THRESHOLD),
        "H_ms_per_decode_step": res["H_ms_per_decode_step"], "decode_ms_per_decode_step_mean": res["decode_ms_per_decode_step_mean"],
        "per_launch_H_fraction": res["per_launch_H_fraction"],
        "host_result_agrees": hx == H and res["gate_G2"] == "PASS" and res["label"] == VERDICT,
        "independent_rederivations_agree": all(clean_h.get(x["seq"]) == x["h_ns"] for x in per) and fr["decision"]["H_exact"] == f"{H.numerator}/{H.denominator}",
        "acquisition_checks": acq,
        "identity": {"runtime_sha256": json.loads((HOST / "IDENTITY_1B.json").read_text(encoding="utf-8"))["runtime_sha256"],
                     "model_sha256": json.loads((G1_DIR / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))["model"]["sha256"],
                     "store_sha256": json.loads((G1_DIR / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))["store"]["sha256"],
                     "phase1b_readiness_sha256": P1B_READINESS, "phase1b_readiness_commit": READINESS_COMMIT, "g1_freeze_sha256": G1_FREEZE},
        "interpretation": "H is an optimistic, loose upper bound on the expert-transfer wait that moving the wait could hide (it ignores "
                          "interference, and W spans to the layer's last expert node). It is an opening gate only: it is NOT an achieved, "
                          "measured or achievable runtime improvement, and it is never reported as one (R5-00 anti_rescue). PASS only "
                          "permits Phase 2 to be prepared and opened by its own separate, explicitly authorized step.",
        "disclosures": {
            "units": "H = sum of H_layer (ms) / sum of predicted_ms of the same 8 launches, the PROTOCOL_1B operational choice fixed before "
                     "acquisition. The frozen ENDPOINTS wording (sum of H_layer / decode tokens as a fraction of mean decode ms/token, with "
                     "decode ms/token = predicted_ms / predicted_n and 511 decode graphs per launch) equals this H x 512/511 = "
                     f"{float(H * Fraction(512, 511)):.10f}; reported for completeness (every H value computed in R5 is reported); "
                     "the classification is the same under both readings and H is not recomputed for any decision",
            "monitor_display": "the Phase 1b `status` command listed the slot that was still running (an attempt not yet recorded) under "
                               "'failed:'. This was display-only: `run` evaluates slot states only at start-up, and the log shows 9 attempts, "
                               "9 launch records, no rerun and no stop. The frozen Phase 1b files are not changed; the Phase 2 monitor reports "
                               "running and post-processing slots separately",
            "v5_comparator": "G1 baseline fidelity used the reconstructed llama-research-v5 comparator (not byte-identical to the lost "
                             "original); this does not enter H, which uses only the frozen R5 binary",
            "trace_overhead": "H is computed on traced B launches; tracing perturbs timing (EXP-024), so H's denominator is traced decode time",
        },
        "scope": "development prompts only; evaluation LOCKED; no Phase 2 artifact; no timing contrast between arms",
    })
    return build_freeze()


def _clean_per_launch(cr: Dict[str, Any]) -> Dict[str, int]:
    for key in ("per_launch", "launches"):
        if key in cr:
            v = cr[key]
            if isinstance(v, dict):
                return {k: int(x["h_ns"] if isinstance(x, dict) else x) for k, x in v.items()}
            return {str(x["seq"]): int(x["h_ns"]) for x in v}
    raise SystemExit("clean-room result has no per-launch values")


def write(name: str, obj: Any) -> None:
    (HERE / name).write_text(dumps(obj), encoding="utf-8", newline="\n")


def sealed_files() -> List[Path]:
    fixed = [HERE / n for n in ("MANIFEST.json", "REDERIVATION.json", "G2_SEAL.json", "README.md", "g2_seal.py", "independent/clean_room_h.py")]
    return fixed + sorted(p for p in HOST.rglob("*") if p.is_file())


def build_freeze() -> Dict[str, Any]:
    rec = {"artifact": "R5-02-G2-FREEZE", "state": "R5_G2_SEALED", "verdict": VERDICT, "eval_state": "LOCKED",
           "phase0": {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE},
           "g1_freeze_sha256": G1_FREEZE, "phase1b_readiness_sha256": P1B_READINESS,
           "files_lf_sha256": {p.relative_to(HERE).as_posix(): lf_sha(p) for p in sealed_files()}}
    rec["freeze_sha256"] = sha_bytes(canon(rec))
    write("FREEZE.json", rec)
    return rec


# ------------------------------------------------------------------------------------------------ verify
def verify(host_dir: Optional[Path] = None, rederive: bool = False) -> List[str]:
    p: List[str] = []
    fz = json.loads((HERE / "FREEZE.json").read_text(encoding="utf-8"))
    if fz["freeze_sha256"] != sha_bytes(canon({k: v for k, v in fz.items() if k != "freeze_sha256"})):
        p.append("FREEZE self-hash")
    on_disk = {q.relative_to(HERE).as_posix() for q in sealed_files()}
    if on_disk != set(fz["files_lf_sha256"]):
        p.append(f"sealed file set differs: {sorted(on_disk ^ set(fz['files_lf_sha256']))[:5]}")
    for n, h in fz["files_lf_sha256"].items():
        q = HERE / n
        if not q.is_file() or lf_sha(q) != h:
            p.append(f"{n} differs from FREEZE")
    if (fz["state"], fz["verdict"], fz["eval_state"], fz["g1_freeze_sha256"], fz["phase1b_readiness_sha256"]) != \
            ("R5_G2_SEALED", VERDICT, "LOCKED", G1_FREEZE, P1B_READINESS):
        p.append("freeze state or upstream digests")
    p0 = json.loads((P0_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if (p0["contract_digest"], p0["freeze_sha256"]) != (P0_CONTRACT_DIGEST, P0_FREEZE) or fz["phase0"] != {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE}:
        p.append("Phase 0 digests")
    g1 = json.loads((G1_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if g1["freeze_sha256"] != G1_FREEZE:
        p.append("G1 seal digest")
    rd = json.loads((P1B / "READINESS.json").read_text(encoding="utf-8"))
    if rd["readiness_sha256"] != P1B_READINESS:
        p.append("Phase 1b readiness digest changed")
    # the verdict: exact H from the per-launch integer sums and the logged timings, against the host result and the threshold
    recs = records(HOST)
    rdv = json.loads((HERE / "REDERIVATION.json").read_text(encoding="utf-8"))
    seal_rec = json.loads((HERE / "G2_SEAL.json").read_text(encoding="utf-8"))
    res = json.loads((HOST / "G2_RESULT.json").read_text(encoding="utf-8"))
    H = exact_h(rdv["per_launch"], recs)
    hx = Fraction(*map(int, res["H_exact"].split("/")))
    if H != hx or f"{H.numerator}/{H.denominator}" != seal_rec["H_exact"] or rdv["H_exact"] != seal_rec["H_exact"]:
        p.append("exact H differs between the re-derivation, the seal and the host result")
    if not (H >= THRESHOLD) or seal_rec["verdict"] != VERDICT or res["label"] != VERDICT:
        p.append("G2 verdict")
    if not rdv["agreement"] or {str(x["seq"]): x["h_ns"] for x in rdv["per_launch"]} != {str(k): v for k, v in rdv["clean_room"]["per_launch_h_ns"].items()}:
        p.append("clean-room and frozen re-derivations disagree")
    tk = {r["seq"]: r for r in recs if r.get("type") == "launch" and r.get("counted")}
    for x in rdv["per_launch"]:
        r = tk.get(x["seq"])
        if r is None or r["trace"]["gz_sha256"] != x["gz_sha256"] or r["trace"]["sha256"] != x["raw_sha256"] or r["trace"]["bytes"] != x["raw_bytes"]:
            p.append(f"re-derived trace identity differs from the log: seq {x['seq']}")
    acq = acquisition_checks(HOST)
    if not all(acq.values()) or acq != seal_rec["acquisition_checks"]:
        p.append(f"acquisition checks: {[k for k, v in acq.items() if not v]}")
    # hygiene
    ev = [x["id"] for x in json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]]
    for q in sealed_files():
        t = q.read_text(encoding="utf-8", errors="replace")
        if any(i in t for i in ev):
            p.append(f"evaluation prompt id in {q.relative_to(HERE)}")
        users = ":" + "\\" + "Users" + "\\"
        if users in t or users.replace("\\", "\\\\") in t:
            p.append(f"unsanitized user path in {q.relative_to(HERE)}")
    man = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
    for e in man["files"]:
        if e["committed"] and lf_sha(HERE / e["committed_path"]) != e["committed_lf_sha256"]:
            p.append(f"committed copy differs from MANIFEST: {e['path']}")
    gz = {e["path"]: e for e in man["files"] if e["path"].startswith("traces/")}
    if sorted(gz) != sorted(f"traces/{x['trace_gz']}" for x in rdv["per_launch"]) or any(gz[f"traces/{x['trace_gz']}"]["raw_sha256"] != x["gz_sha256"] for x in rdv["per_launch"]):
        p.append("MANIFEST trace hashes differ from the re-derivation")
    if host_dir is not None:
        raw = {q.relative_to(host_dir).as_posix(): q for q in evidence_files(host_dir)}
        if set(raw) != {e["path"] for e in man["files"] if e["path"] != MARKER}:
            p.append("host directory file set differs from MANIFEST")
        for e in man["files"]:
            q = raw.get(e["path"])
            if e["path"] == MARKER:
                continue
            if q is None or q.stat().st_size != e["bytes"] or sha_file(q) != e["raw_sha256"]:
                p.append(f"host file differs from MANIFEST: {e['path']}")
        if rederive:
            HR = _load("headroom", P1B / "headroom.py")
            for x in rdv["per_launch"]:
                got = HR.launch_h(HR.read_lines(host_dir / "traces" / x["trace_gz"]), expected_decode_tokens=x["predicted_n"] - 1).h_ns
                if got != x["h_ns"]:
                    p.append(f"frozen estimator gives {got} for seq {x['seq']}, sealed {x['h_ns']}")
    return p


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("seal")
    for k in ("--host-dir", "--marker", "--profile-prefix", "--rederived", "--clean-room"):
        s.add_argument(k, required=True)
    v = sp.add_parser("verify"); v.add_argument("--host-dir"); v.add_argument("--rederive", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "seal":
        print("freeze_sha256", seal(Path(a.host_dir), Path(a.marker), a.profile_prefix, Path(a.rederived), Path(a.clean_room))["freeze_sha256"])
        return 0
    if a.rederive and not a.host_dir:
        ap.error("--rederive needs --host-dir")
    pr = verify(Path(a.host_dir) if a.host_dir else None, a.rederive)
    print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]
    return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
