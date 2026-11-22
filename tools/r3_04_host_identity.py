"""R3-04 Gate 3 host identity attestation (MODEL-FREE: reads and hashes files only; never launches llama-server, never opens a socket, no subprocess).

  python tools\\r3_04_host_identity.py --runtime <llama-server.exe> --out <new json>

Hashes the frozen model, expert store and runtime executable on the host and compares them with the frozen values (ACQUISITION_PLAN.json model/store; V.RUNTIME_SHA256).
Writes one attestation JSON (refuses to overwrite). Exit 0 only if everything matches. No value is ever filled in from anywhere except the files themselves.
"""
from __future__ import annotations

import argparse, hashlib, json, sys, time
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "experiments" / "R3-02-calibration-mechanism" / "ACQUISITION_PLAN.json"
RUNTIME_SHA256 = "8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5"
SCHEMA = "R3-04-HOST-IDENTITY"
CHUNK = 8 * 1024 * 1024


def hash_file(path: Path) -> Dict[str, Any]:
    h = hashlib.sha256(); n = 0
    with path.open("rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b); n += len(b)
    return {"absolute_path": str(path.resolve()), "size_bytes": n, "sha256": h.hexdigest()}


def attest(model: Path, store: Path, runtime: Path, plan: Dict[str, Any]) -> Dict[str, Any]:
    rows = {}
    for name, p, exp_bytes, exp_sha in (("model", model, plan["model"]["bytes"], plan["model"]["sha256"]), ("store", store, plan["store"]["bytes"], plan["store"]["sha256"]), ("runtime", runtime, None, RUNTIME_SHA256)):
        if not p.is_file():
            raise FileNotFoundError(f"{name}: {p} is not a file")
        r = hash_file(p)
        r.update(expected_sha256=exp_sha, expected_size_bytes=exp_bytes, sha256_matches=r["sha256"] == exp_sha, size_matches=(exp_bytes is None or r["size_bytes"] == exp_bytes))
        rows[name] = r
    ok = all(r["sha256_matches"] and r["size_matches"] for r in rows.values())
    return {"artifact": SCHEMA, "model_free": True, "llama_server_launched": False, "created_unix": time.time(), "files": rows, "plan_model_path": plan["model"]["path"], "plan_store_path": plan["store"]["path"],
            "verdict": "IDENTITY_MATCHES_FROZEN_PLAN" if ok else "IDENTITY_MISMATCH"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--runtime", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--model"); ap.add_argument("--store")
    a = ap.parse_args(argv)
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    out = Path(a.out)
    if out.exists():
        print("REFUSED: --out exists"); return 1
    att = attest(Path(a.model or plan["model"]["path"]), Path(a.store or plan["store"]["path"]), Path(a.runtime), plan)
    out.write_text(json.dumps(att, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for k, r in att["files"].items():
        print(f"{k}: {r['absolute_path']}  {r['size_bytes']} bytes  sha256={r['sha256']}  match={r['sha256_matches'] and r['size_matches']}")
    print(att["verdict"]); return 0 if att["verdict"] == "IDENTITY_MATCHES_FROZEN_PLAN" else 1


if __name__ == "__main__":
    sys.exit(main())
