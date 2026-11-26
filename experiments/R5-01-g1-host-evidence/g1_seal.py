"""Research 5 Phase 1a -- sealing of the canonical G1 host-conformance evidence and the IMPLEMENTATION_FREEZE.

    python g1_seal.py seal --host-dir DIR --profile-prefix PREFIX   (once, by the sealing step; needs the host files)
    python g1_seal.py verify [--host-dir DIR]                        (any time; --host-dir also re-checks the raw files)

`seal` copies the canonical host output directory into host/ with ONE deterministic transform: the user-profile
prefix of the host paths (PREFIX, e.g. the value of %USERPROFILE%; given on the command line so that no personal path
is ever written into the repository) is replaced by the token %USERPROFILE%, in its plain and JSON-escaped spellings.
It records the raw SHA-256 and size of every original file (MANIFEST.json), the recomputed G1 verdict (G1_SEAL.json),
the IMPLEMENTATION_FREEZE required by R5-00 DECISION_RULES (IMPLEMENTATION_FREEZE.json) and FREEZE.json.

`verify` recomputes the G1 verdict from the committed copies with the committed driver (host_conformance.evaluate) and
checks every hash, every identity and the freeze of the runtime sources: after this seal, any change to the frozen R5
runtime sources, binaries, model or store makes verification fail. No timing contrast between arms is computed here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
R501 = REPO / "experiments" / "R5-01-ets-implementation"
P0_DIR = REPO / "experiments" / "R5-00-exposed-transfer-stall"
BUILD_INFO = REPO / "llama-research-r5-ets-2" / "BUILD-INFO.json"
HOST = HERE / "host"
TOKEN = "%USERPROFILE%"
P0_CONTRACT_DIGEST = "9d480ccf1b5109fa9be6cd717261e824876122e1ec504a3ccd85e0e30e619e52"
P0_FREEZE = "21ed630b638ca56a19b6a392a6ce1e47782dcd4351b178d523d5e28a6c7f63ee"
SOURCE_COMMIT = "4a7f308"     # the commit whose runtime ran the canonical G1 campaign (Windows TLS correction)
# committed: the driver records; not committed: the model-free test's scratch (synthetic store and its traces, hashed)
COMMITTED = ("DEV_LOG.jsonl", "G1_HOST_RESULT.json", "IDENTITY.json", "PREREQUISITES.json", "STARTUP_SMOKE.json", "WINDOWS_MODEL_FREE.json")
EXPECTED_LAUNCHES = 42
VERDICT = "R5_G1_HOST_CONFORMANCE_PASS"
NONCANONICAL = {
    "phase1a-host": "first attempt (2026-11-23): refused at the v5 identity check before any launch (lost v5 executable); empty",
    "phase1a-host-v5rec": "second attempt (2026-11-25): Windows model-free test died with 0xC0000374, the old driver went on; 2 launches "
                          "finished and a third was interrupted under the superseded runtime e8c94da8... (G1-ATTEMPT-1-INCIDENT.md); "
                          "excluded, never resumed (the driver refuses that directory)",
    "ets-modelfree-v2": "minimal host validation of the corrected test executable before the campaign: exit 0, RESULT 143/0/3 (model-free only)",
}


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def sha_file(p: Path) -> str: return sha_bytes(p.read_bytes())
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


def driver():
    return _load("host_conformance", R501 / "host_conformance.py")


def sanitize(b: bytes, prefix: str) -> bytes:
    """The ONE transform applied to committed copies: the user-profile prefix -> %USERPROFILE% (plain and JSON-escaped)."""
    p = prefix.rstrip("\\/")
    for v in (p.replace("\\", "\\\\"), p, p.replace("\\", "/")):
        b = b.replace(v.encode("utf-8"), TOKEN.encode("utf-8"))
    return b


def evidence_files(host_dir: Path) -> List[Path]:
    return sorted(p for p in host_dir.rglob("*") if p.is_file())


def committed_rel(rel: str) -> bool:
    return rel in COMMITTED or (rel.startswith("launches/") and rel.endswith(".log"))


def recompute_verdict(host: Path) -> Dict[str, Any]:
    """The committed driver's evaluate() on the committed copies (no timing contrast between arms is produced)."""
    H = driver()
    recs = [json.loads(l) for l in (host / "DEV_LOG.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    mf = json.loads((host / "WINDOWS_MODEL_FREE.json").read_text(encoding="utf-8"))
    sm = json.loads((host / "STARTUP_SMOKE.json").read_text(encoding="utf-8"))
    return H.evaluate(recs, mf, sm)


def implementation_freeze(ident: Dict[str, Any]) -> Dict[str, Any]:
    imp = json.loads((R501 / "IMPLEMENTATION.json").read_text(encoding="utf-8"))
    bi = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    return {
        "artifact": "R5-01-IMPLEMENTATION-FREEZE",
        "rule": "R5-00 DECISION_RULES.json gates.IMPLEMENTATION_FREEZE: before G2, source hashes, binary hash, model and store hashes and the "
                "implementation choices are frozen; no change afterwards",
        "frozen_after": "the canonical G1 host campaign (R5_G1_HOST_CONFORMANCE_PASS)",
        "source_commit": SOURCE_COMMIT,
        "runtime_sources_lf_sha256": bi["sources_lf_sha256"],
        "frozen_runtime_sources_reused_lf_sha256": imp["frozen_runtime_sources_reused_lf_sha256"],
        "build_info": "llama-research-r5-ets-2/BUILD-INFO.json",
        "binaries_sha256": bi["binaries"],
        "binaries_pe_normalized_sha256": bi["binaries_pe_normalized_sha256"],
        "model": {"file": "Qwen_Qwen3-30B-A3B-Q4_K_M.gguf", "sha256": ident["model_sha256"]},
        "store": {"file": "Qwen_Qwen3-30B-A3B-Q4_K_M.psxs", "sha256": ident["store_sha256"]},
        "v5_comparator": {"kind": ident["v5_identity"]["kind"], "raw_sha256": ident["v5_identity"]["raw_sha256"],
                          "pe_normalized_sha256": ident["v5_identity"]["pe_normalized_sha256"],
                          "historical_raw_sha256": ident["v5_historical_sha256"],
                          "disclosure": "the historical llama-research-v5 executable was lost; G1 used the deterministic reconstruction "
                                        "frozen in V5-RECOVERY.json, which is NOT byte-identical to the original"},
        "implementation_choices": imp["implementation_choices"],
        "arms": imp["arms"],
    }


def seal(host_dir: Path, profile_prefix: str) -> Dict[str, Any]:
    files = evidence_files(host_dir)
    user = Path(profile_prefix.replace("\\", "/")).name
    if HOST.exists():
        shutil.rmtree(HOST)
    manifest = []
    for p in files:
        rel = p.relative_to(host_dir).as_posix()
        raw = p.read_bytes()
        e = {"path": rel, "bytes": len(raw), "raw_sha256": sha_bytes(raw), "committed": committed_rel(rel)}
        if e["committed"]:
            s = sanitize(raw, profile_prefix)
            if user and user.encode() in s:
                raise SystemExit(f"personal path left in {rel} after sanitizing")
            dst = HOST / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(lf(s))
            e["committed_path"] = f"host/{rel}"
            e["committed_lf_sha256"] = sha_bytes(lf(s))
            e["sanitized"] = s != raw
        manifest.append(e)
    ident = json.loads((HOST / "IDENTITY.json").read_text(encoding="utf-8"))
    res = recompute_verdict(HOST)
    host_res = json.loads((HOST / "G1_HOST_RESULT.json").read_text(encoding="utf-8"))
    recs = [json.loads(l) for l in (HOST / "DEV_LOG.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    write("MANIFEST.json", {
        "artifact": "R5-01-G1-EVIDENCE-MANIFEST",
        "canonical_host_directory": f"{TOKEN}\\AppData\\Local\\Argine\\r5\\phase1a-host-g1-v2 (on the Windows host; kept, not deleted)",
        "files": manifest, "n_files": len(manifest), "bytes_total": sum(e["bytes"] for e in manifest),
        "committed_transform": f"user-profile prefix -> {TOKEN} (plain and JSON-escaped), CRLF -> LF; nothing else",
        "not_committed": "ets-test-scratch/ (the model-free test's synthetic store and traces): hashed here, kept on the host; "
                         "traces/ is empty by design (G1 reduced each raw trace to its structure and deleted it; sizes and hashes are in DEV_LOG)",
        "noncanonical_directories": NONCANONICAL,
    })
    write("IMPLEMENTATION_FREEZE.json", implementation_freeze(ident))
    write("G1_SEAL.json", {
        "artifact": "R5-01-G1-SEAL",
        "verdict": res["verdict"], "criteria": res["criteria"], "n_criteria": len(res["criteria"]),
        "criteria_true": sum(1 for v in res["criteria"].values() if v),
        "verdict_recomputed_with": "experiments/R5-01-ets-implementation/host_conformance.py evaluate() on host/ (committed copies)",
        "host_result_agrees": res["verdict"] == host_res["verdict"] and res["criteria"] == host_res["criteria"],
        "host_result_float_note": "the host and recomputed files differ only in the last digit of two floating-point summaries "
                                  "(different Python builds); verdict and all criteria are identical",
        "launches": len(recs), "launch_seqs": sorted(r["seq"] for r in recs),
        "identity": {k: ident[k] for k in ("r5_runtime_sha256", "ets_test_sha256", "v5_runtime_sha256", "v5_historical_sha256",
                                           "model_sha256", "store_sha256")},
        "v5_reconstruction_disclosure": "the v5 comparator was RECONSTRUCTED from the frozen v5 source/toolchain/build contract "
                                        "(V5-RECOVERY.json), not recovered byte for byte; historical raw SHA-256 65f445c4... unused",
        "provenance": {"windows_tls_incident": "experiments/R5-01-ets-implementation/G1-ATTEMPT-1-INCIDENT.md: the second attempt's "
                       "model-free test died with STATUS_HEAP_CORRUPTION; corrected in 4a7f308 (argine-tls.h); this campaign ran the corrected "
                       "build after the minimal model-free validation passed",
                       "noncanonical_directories": NONCANONICAL},
        "redundant_transfers_coverage": "the frozen G1 text also requires 0 redundant transfers, which is not one of the driver's 17 criteria: "
                                        "covered by the model-free conformance test (identical requests, bytes, eviction and LRU order; "
                                        "MODEL_FREE_CONFORMANCE.json) and, in the 2 traced launches, by read records equal to store misses",
        "independent_verification": {"method": "separate agent, own code (not evaluate()), read-only on the host files; no arm timing contrast",
                                     "conclusion": "CONFIRMED: 42/42 slots match the schedule; 17/17 criteria re-derived, 0 disagreements; "
                                                   "max purity deviation 0.36% (limit 1%); all safety counters 0; interrupted attempt isolated",
                                     "notes": ["server predicted_per_token_ms divides by 511 while predicted_n = 512 (server convention); "
                                               "the driver uses predicted_ms / predicted_n consistently",
                                               "repeated A launches differ slightly in misses (multithreaded nondeterminism), the same scale as the "
                                               "purity deviations, all within 1%"]},
        "scope": "implementation and host-conformance evidence on the four development prompts; NOT a runtime speedup; no timing contrast "
                 "between C or C0 and A is computed or reported; evaluation LOCKED; H (Phase 1b) not computed",
    })
    return build_freeze()


def write(name: str, obj: Any) -> None:
    (HERE / name).write_text(dumps(obj), encoding="utf-8", newline="\n")


def sealed_files() -> List[Path]:
    fixed = [HERE / n for n in ("MANIFEST.json", "IMPLEMENTATION_FREEZE.json", "G1_SEAL.json", "README.md", "g1_seal.py")]
    return fixed + sorted(p for p in HOST.rglob("*") if p.is_file())


def build_freeze() -> Dict[str, Any]:
    rec = {"artifact": "R5-01-G1-FREEZE", "state": "R5_G1_SEALED", "verdict": VERDICT, "eval_state": "LOCKED",
           "phase0": {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE},
           "files_lf_sha256": {p.relative_to(HERE).as_posix(): lf_sha(p) for p in sealed_files()}}
    rec["freeze_sha256"] = sha_bytes(canon(rec))
    write("FREEZE.json", rec)
    return rec


def verify(host_dir: Path = None) -> List[str]:
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
    if (fz["state"], fz["verdict"], fz["eval_state"]) != ("R5_G1_SEALED", VERDICT, "LOCKED"):
        p.append("freeze state")
    p0 = json.loads((P0_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if (p0["contract_digest"], p0["freeze_sha256"]) != (P0_CONTRACT_DIGEST, P0_FREEZE) or fz["phase0"] != {"contract_digest": P0_CONTRACT_DIGEST, "freeze_sha256": P0_FREEZE}:
        p.append("Phase 0 digests")
    seal_rec = json.loads((HERE / "G1_SEAL.json").read_text(encoding="utf-8"))
    res = recompute_verdict(HOST)
    if res["verdict"] != VERDICT or seal_rec["verdict"] != VERDICT or res["criteria"] != seal_rec["criteria"] or not all(res["criteria"].values()):
        p.append("recomputed G1 verdict or criteria differ from the seal")
    if seal_rec["n_criteria"] != 17 or seal_rec["launches"] != EXPECTED_LAUNCHES or seal_rec["launch_seqs"] != list(range(1, EXPECTED_LAUNCHES + 1)):
        p.append("criteria or launch count")
    # identities: driver pins, BUILD-INFO, V5-RECOVERY, IMPLEMENTATION_FREEZE and the campaign's IDENTITY agree
    H = driver()
    ident = json.loads((HOST / "IDENTITY.json").read_text(encoding="utf-8"))
    bi = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    imf = json.loads((HERE / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))
    v5 = json.loads((R501 / "V5-RECOVERY.json").read_text(encoding="utf-8"))["accepted_reconstruction"]
    if not (ident["r5_runtime_sha256"] == H.R5_AUTHORIZED["llama-server.exe"] == bi["binaries"]["llama-server.exe"] == imf["binaries_sha256"]["llama-server.exe"]):
        p.append("R5 runtime identity")
    if not (ident["ets_test_sha256"] == H.R5_AUTHORIZED["argine-ets-test.exe"] == bi["binaries"]["argine-ets-test.exe"]):
        p.append("R5 test identity")
    if not (ident["v5_runtime_sha256"] == v5["reconstructed_raw_sha256"] == H.V5_RECONSTRUCTED["reconstructed_raw_sha256"] == imf["v5_comparator"]["raw_sha256"]):
        p.append("v5 comparator identity")
    if (imf["model"]["sha256"], imf["store"]["sha256"]) != (ident["model_sha256"], ident["store_sha256"]):
        p.append("model / store identity")
    # the freeze: the runtime sources in the repository must still be the frozen ones
    for rel, h in imf["runtime_sources_lf_sha256"].items():
        if lf_sha(REPO / rel) != h:
            p.append(f"frozen runtime source changed after the IMPLEMENTATION_FREEZE: {rel}")
    for rel, h in imf["frozen_runtime_sources_reused_lf_sha256"].items():
        if lf_sha(REPO / rel) != h:
            p.append(f"frozen reused source changed: {rel}")
    if imf != json.loads(dumps(implementation_freeze(ident))):
        p.append("IMPLEMENTATION_FREEZE differs from the record rebuilt from the repository")
    # hygiene: no evaluation prompt id, no personal path token other than %USERPROFILE%
    ev = [x["id"] for x in json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))["evaluation"]["prompts"]]
    for q in sealed_files():
        t = q.read_text(encoding="utf-8", errors="replace")
        if any(i in t for i in ev):
            p.append(f"evaluation prompt id in {q.relative_to(HERE)}")
        users = ":" + "\\" + "Users" + "\\"          # built at run time so that this file does not match itself
        if users in t or users.replace("\\", "\\\\") in t:
            p.append(f"unsanitized user path in {q.relative_to(HERE)}")
    man = json.loads((HERE / "MANIFEST.json").read_text(encoding="utf-8"))
    for e in man["files"]:
        if e["committed"] and lf_sha(HERE / e["committed_path"]) != e["committed_lf_sha256"]:
            p.append(f"committed copy differs from MANIFEST: {e['path']}")
    if host_dir is not None:     # raw re-check against the preserved host directory
        raw = {q.relative_to(host_dir).as_posix(): q for q in evidence_files(host_dir)}
        if set(raw) != {e["path"] for e in man["files"]}:
            p.append("host directory file set differs from MANIFEST")
        for e in man["files"]:
            q = raw.get(e["path"])
            if q is None or q.stat().st_size != e["bytes"] or sha_file(q) != e["raw_sha256"]:
                p.append(f"host file differs from MANIFEST: {e['path']}")
    return p


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("seal"); s.add_argument("--host-dir", required=True); s.add_argument("--profile-prefix", required=True)
    v = sp.add_parser("verify"); v.add_argument("--host-dir")
    a = ap.parse_args(argv)
    if a.cmd == "seal":
        print("freeze_sha256", seal(Path(a.host_dir), a.profile_prefix)["freeze_sha256"]); return 0
    pr = verify(Path(a.host_dir) if a.host_dir else None)
    print("VERIFY", "OK" if not pr else "FAIL"); [print(" -", x) for x in pr]
    return 1 if pr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
