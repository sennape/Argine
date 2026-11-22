"""R3-04 Gate 3: create OPENING_FREEZE.json. Model-free. Creates NO campaign state (no output directory, no launch).

  python tools/r3_04_open_gate3.py --attestation <R3-04-host-identity.json> --driver-commit <git commit> [--dry-run]

Refuses unless: protocol freeze, Gate 2 freeze (MECHANISM_PASS + helper validated), amendment freeze and amended schedule all verify; the host attestation says the frozen model/store/runtime
hashes and sizes match; the driver file is committed unmodified at --driver-commit. Refuses to overwrite an existing OPENING_FREEZE.json.
"""
from __future__ import annotations

import argparse, datetime as dt, json, subprocess, sys
from pathlib import Path
from typing import Any, Dict, Optional

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import r3_04_acquire as A  # noqa: E402
import r3_04_host_identity as HI  # noqa: E402

V, REPO = A.V, A.REPO
PROTOCOL_FREEZE_SHA256 = "a88359b52891f93ec49e67978d92c2eb3b73db588611bce46b3103b2978c1202"
AMENDMENT_FREEZE_SHA256 = "ea0d6719f6d4f92dd77b597662ea3903bc22db70647e37dd8b4cf9ec06c9b3f6"
GATE2_FREEZE_SHA256 = "59fd7ac62242e8d126f6a8d7b6b31586b82119f9f8afccd5af20ded9237b2958"
RUNTIME_PATH = r"C:\Users\alpel\Desktop\Argine4-r3build-r3-03b\llama.cpp\build-r3-03b\bin\llama-server.exe"
DRIVER = TOOLS / "r3_04_acquire.py"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()      # no shell


def check_attestation(att: Dict[str, Any], plan: Dict[str, Any]) -> None:
    if att.get("artifact") != HI.SCHEMA or att.get("model_free") is not True or att.get("llama_server_launched") is not False or att.get("verdict") != "IDENTITY_MATCHES_FROZEN_PLAN":
        raise SystemExit("REFUSED: attestation is not a matching model-free R3-04 host identity attestation")
    f = att["files"]
    for name, exp_path, exp_sha, exp_bytes in (("model", plan["model"]["path"], plan["model"]["sha256"], plan["model"]["bytes"]), ("store", plan["store"]["path"], plan["store"]["sha256"], plan["store"]["bytes"]),
                                               ("runtime", RUNTIME_PATH, V.RUNTIME_SHA256, None)):
        r = f.get(name) or {}
        if r.get("sha256") != exp_sha or r.get("expected_sha256") != exp_sha or not r.get("sha256_matches") or not r.get("size_matches"):
            raise SystemExit(f"REFUSED: attestation {name} sha256/size does not match the frozen value")
        if exp_bytes is not None and r.get("size_bytes") != exp_bytes:
            raise SystemExit(f"REFUSED: attestation {name} size differs from the frozen size")
        if str(r.get("absolute_path", "")).lower() != exp_path.lower():
            raise SystemExit(f"REFUSED: attestation {name} path differs from the frozen path")


def build(att: Dict[str, Any], driver_commit: str, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    pf = A.load_protocol_freeze(PROTOCOL_FREEZE_SHA256)
    am, sched = A.load_amendment(AMENDMENT_FREEZE_SHA256, pf["freeze_sha256"])
    g2 = json.loads((A.GATE2_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if V.freeze_sha(g2) != g2["freeze_sha256"] or g2["freeze_sha256"] != GATE2_FREEZE_SHA256 or g2["classification"] != "R3_04_MECHANISM_PASS" \
            or g2["shutdown_helper_status"] != "R3_04_SHUTDOWN_HELPER_VALIDATED_ON_REAL_MODEL":
        raise SystemExit("REFUSED: Gate 2 freeze does not verify")
    plan = json.loads(HI.PLAN.read_text(encoding="utf-8"))
    check_attestation(att, plan)
    full = git("rev-parse", driver_commit); last = git("log", "-1", "--format=%H", "--", "tools/r3_04_acquire.py")
    if full != last or git("status", "--porcelain", "--", "tools/r3_04_acquire.py"):
        raise SystemExit("REFUSED: --driver-commit is not the last commit of the driver, or the driver has uncommitted changes")
    if A.OPENING_FREEZE.exists():
        raise SystemExit("REFUSED: OPENING_FREEZE.json already exists")
    now = now or dt.datetime.now(dt.timezone.utc)
    of: Dict[str, Any] = {
        "artifact": "R3-04-OPENING-FREEZE", "status": "OPENING_FROZEN_CAMPAIGN_NOT_AUTHORIZED", "campaign_authorized": False, "authorization_required_from": "Luca (separate explicit authorization)",
        "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "head_commit_at_creation": git("rev-parse", "HEAD"),
        "protocol_freeze_sha256": pf["freeze_sha256"], "protocol_commit": am["parents"]["protocol_commit"], "gate2_freeze_sha256": g2["freeze_sha256"], "gate2_classification": g2["classification"], "gate2_shutdown_helper_status": g2["shutdown_helper_status"],
        "gate2_evidence_commit": am["parents"]["gate2_evidence_commit"], "amendment_freeze_sha256": AMENDMENT_FREEZE_SHA256, "amendment_design": am["design"]["name"],
        "driver_path": "tools/r3_04_acquire.py", "driver_sha256_lf": V.sha_lf(DRIVER), "driver_sha256_raw": V.sha_file(DRIVER), "driver_commit": full,
        "runtime_sha256": V.RUNTIME_SHA256, "runtime_path": RUNTIME_PATH, "model": {"path": plan["model"]["path"], "sha256": att["files"]["model"]["sha256"], "size_bytes": att["files"]["model"]["size_bytes"]},
        "store": {"path": plan["store"]["path"], "sha256": att["files"]["store"]["sha256"], "size_bytes": att["files"]["store"]["size_bytes"]},
        "host_identity_attestation": {"verdict": att["verdict"], "created_unix": att["created_unix"], "model_free": True},
        "amended_schedule_sha256": V.schedule_sha(sched), "frozen_schedule_sha256": pf["timing_schedule_sha256"],
        "physical_launches": len(sched), "counted_launches": sum(s["kind"] == "counted" for s in sched), "warmups": sum(s["kind"] == "warmup" for s in sched),
        "blocks": {"1.5": 18, "8": 6}, "timing_4GiB": "omitted (0 launches)", "primary_budget": "1.5", "primary_n_blocks": 18,
        "shutdown_method_required": "own_console_helper", "helper_fallback": "fails the launch closed", "clean_environment_attestation_required": True,
        "fixed_n": True, "optional_stopping": False, "resume_semantics": "deterministic: accepted prefix by atomic records, orphans classified non-counted, one rerun of the same slot then abort, no replacement, no imputation",
        "analysis_before_complete_acquisition": "refused", "primary_endpoint": "decode wall ms/token", "primary_estimator": "frozen paired block t-interval over DELTA_NET = P - A (V.classify_primary)", "confidence_level": 0.95,
        "classification_rule": {"NET_WIN": "95% CI upper bound < 0", "NET_LOSS": "95% CI lower bound > 0", "INCONCLUSIVE": "CI contains zero"}}
    of["freeze_sha256"] = V.freeze_sha(of)
    return of


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--attestation", required=True); ap.add_argument("--driver-commit", required=True); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    of = build(json.loads(Path(a.attestation).read_text(encoding="utf-8")), a.driver_commit)
    if a.dry_run:
        print(json.dumps(of, indent=1, sort_keys=True)); return 0
    A.OPENING_FREEZE.write_text(json.dumps(of, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("OPENING_FREEZE", of["freeze_sha256"]); return 0


if __name__ == "__main__":
    sys.exit(main())
