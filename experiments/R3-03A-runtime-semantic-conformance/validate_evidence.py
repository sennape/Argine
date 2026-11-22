"""Re-validate the R3-03A real-model conformance evidence from the raw host files (no model, no timing).

  python validate_evidence.py <evidence_dir> [--quick]

Checks: (1) file sha256/size vs EVIDENCE_MANIFEST.json; (2) output-token digests recomputed from the stored token ids
vs the frozen R3-02 digest; (3) full streaming replay of every trace against the frozen R2 reference
(tools/r3_03a_trace.py); (4) per-event trajectory identity of the sham trace and the b_selected trace.
--quick runs only (1) and (2).  Exit 0 only if everything holds.
"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "experiments" / "R3-01-telemetry-realization")]
import r3_telemetry.telemetry as T  # noqa: E402
import r3_03a_trace as tr  # noqa: E402

FROZEN_DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
LAUNCHES = {"A-4GiB": False, "B-4GiB": True, "C-8GiB": False}     # value = replay as b_selected (sham)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def sham_vs_b_selected(a_trace, b_trace):
    keys = ("requested_experts", "hit_experts", "miss_experts", "evicted", "resident_after", "policy_resident_bytes_after", "semantic_event")
    n = bad = 0
    with open(a_trace, "rb") as fa, open(b_trace, "rb") as fb:
        for la, lb in zip(fa, fb):
            a, b = json.loads(la), json.loads(lb)
            if "complete" in a or "complete" in b:
                if a != b:
                    bad += 1
                continue
            n += 1
            if a["policy"] != "b_selected" or b["policy"] != "predicted_protect_sham" or any(a[k] != b[k] for k in keys):
                bad += 1
    return {"events": n, "mismatches": bad}


def main(argv):
    d = Path(argv[1]); quick = "--quick" in argv
    man = json.loads((HERE / "EVIDENCE_MANIFEST.json").read_text())
    ok = True
    for name, m in man["files"].items():
        p = d / name
        good = p.is_file() and p.stat().st_size == m["bytes"] and sha256(p) == m["sha256"]
        print("file", name, "OK" if good else "MISMATCH"); ok &= good
    for t in LAUNCHES:
        rec = json.loads((d / f"{t}.launch.json").read_text())
        good = T.output_token_digest(rec["tokens"]) == FROZEN_DIGEST == rec["output_token_digest"] and len(rec["tokens"]) == 512
        print("token digest", t, "OK" if good else "MISMATCH"); ok &= good
    if not quick:
        for t, sham in LAUNCHES.items():
            r = tr.analyse_file(d / f"{t}.trace.jsonl", n_layers=48, expected_passes=514, sham=sham)
            good = bool(r.get("ok")) and r["semantic_events"] == 24672 and r["completion_rows"] == 24672
            print("replay", t, "OK" if good else f"FAIL {r}"); ok &= good
        r = sham_vs_b_selected(d / "A-4GiB.trace.jsonl", d / "B-4GiB.trace.jsonl")
        good = r["events"] == 24672 and r["mismatches"] == 0
        print("sham==b_selected", r, "OK" if good else "FAIL"); ok &= good
    print("VALIDATION", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
