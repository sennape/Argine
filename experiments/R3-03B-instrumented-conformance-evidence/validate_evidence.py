"""Independent validation of the R3-03B instrumented conformance evidence. Stdlib only.

Does NOT import tools/r3_03b_conformance.py or tools/r3_03a_trace.py.
  python validate_evidence.py --quick                      # committed host_records vs EVIDENCE_MANIFEST + every PASS condition
  python validate_evidence.py --traces-dir <dir>           # additionally re-hash the 4 traces and re-scan their semantic structure
Exit 0 = every check passed.
"""
import argparse, collections, hashlib, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAGS = ("A-8GiB-OFF", "A-8GiB-ON", "B-8GiB-OFF", "B-8GiB-ON")
POLICY = {"A": "b_selected", "B": "predicted_protect_sham"}
DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
RUNTIME = "8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5"
OLD = ("e935005e5131b29d12b113b000fa52e798b4b4419a23724ec70af3f6875fcd76", "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644")
PASSES, PER_PASS, EVENTS = 514, 48, 24672
BUDGET = 8 * 1024 ** 3
TIMING_RE = re.compile(r"argine: policy_timing (\{.*\})\s*$", re.M)
EV_RE = re.compile(rb'"policy_resident_bytes_after":(\d+),"budget_bytes":(\d+),.*"semantic_event":\{"schema":"r3-03a-v1","pass":(\d+),"layer":(\d+),"node_kind":(\d+),"first_node":(\d+)\}\}\s*$', re.S)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def scan_trace(path):
    seen, per, over, pol, comp, bad, alt, expect = set(), collections.Counter(), 0, 0, 0, 0, True, False
    with open(path, "rb", buffering=1 << 24) as f:
        for line in f:
            if line.startswith(b'{"step":') and b'"complete":1' in line[:60]:
                comp += 1
                alt &= expect
                expect = False
                continue
            alt &= not expect
            m = EV_RE.search(line[-600:])
            if not m:
                bad += 1
                continue
            pol += 1
            expect = True
            k = (int(m.group(3)), int(m.group(4)))
            seen.add(k)
            per[k[0]] += 1
            over += int(m.group(1)) > int(m.group(2))
    return {"policy_rows": pol, "completion_rows": comp, "unparsed": bad, "alternating": alt, "passes": len(per),
            "events_per_pass": sorted(set(per.values())), "unique_events": len(seen), "budget_violations": over}


def token_digest(tokens):
    """Frozen R3-01 definition: sha256 of b"argine-output-tokens-v1\\n" + each decimal token id + b"\\n"."""
    h = hashlib.sha256(b"argine-output-tokens-v1\n")
    for x in tokens:
        h.update(str(int(x)).encode() + b"\n")
    return h.hexdigest()


def timing_record(text):
    recs = [json.loads(m) for m in TIMING_RE.findall(text)]
    return recs


def run_checks(rec_dir, traces_dir=None):
    out = []

    def chk(n, ok, d=""):
        out.append({"check": n, "ok": bool(ok), "detail": d})

    man = json.loads((HERE / "EVIDENCE_MANIFEST.json").read_text())
    for k, h in man["host_records_sha256"].items():
        chk("manifest:" + k, sha(rec_dir / k) == h)
    res = json.loads((rec_dir / "CONFORMANCE_RESULT.json").read_text())
    chk("result_label", res["result"] == "R3_03B_INSTRUMENTED_CONFORMANCE_PASS")
    chk("runtime_sha_new", res["runtime_sha256"] == RUNTIME and RUNTIME not in OLD)
    chk("no_tooling_issues", res["tooling_issues"] == [])
    chk("no_timing_claim", res["timing_evidence"].startswith("NONE"))
    trace_hash = {}
    for t in TAGS:
        L = json.loads((rec_dir / f"{t}.launch.json").read_text())
        A = json.loads((rec_dir / f"{t}.analysis.json").read_text())
        err = (rec_dir / f"{t}.stderr.log").read_text(encoding="utf-8", errors="replace")
        on = t.endswith("-ON")
        chk(t + ":runtime_path", L["runtime"].endswith(r"Argine4-r3build-r3-03b\llama.cpp\build-r3-03b\bin\llama-server.exe"))
        chk(t + ":policy", L["policy_id"] == POLICY[t[0]] and A["policy_id"] == POLICY[t[0]])
        chk(t + ":digest", L["output_token_digest"] == DIGEST and res["launches"][t]["token_digest"] == DIGEST)
        chk(t + ":n_tokens", L["n_tokens"] == 512 == len(L["tokens"]))
        chk(t + ":digest_recomputed", token_digest(L["tokens"]) == DIGEST)
        chk(t + ":passes_events", A["passes"] == PASSES and A["events_per_pass"] == PER_PASS and A["semantic_events"] == EVENTS
            and A["policy_event_rows"] == EVENTS and A["completion_rows"] == EVENTS)
        chk(t + ":no_dup_no_missing", A["duplicate_events"] == 0 and A["missing_events"] == 0)
        chk(t + ":budget", A["budget_violations"] == 0 and A["max_policy_resident_bytes"] <= BUDGET == A["budget_bytes"]
            and A["physical"]["post_service_over_budget"] == 0)
        chk(t + ":replay", A["r2_replay"] == "MATCH" and A["history_last_ref"] == "MATCH" and A["decay_score_max_abs_diff"] == 0.0)
        chk(t + ":graceful", L["shutdown_mode"] == "graceful")
        recs = timing_record(err)
        if on:
            r = recs[0] if len(recs) == 1 else {}
            chk(t + ":one_timing_record", len(recs) == 1)
            chk(t + ":timing_valid", bool(r) and r["schema"] == "r3-03b-v1" and r["calls"] == EVENTS and len(r["hist_log2"]) == 40
                and sum(r["hist_log2"]) == r["calls"] and 0 < r["ns_total"] and r["ns_prediction_update"] + r["ns_victim_selection"] <= r["ns_total"]
                and r["victim_calls"] == A["total_evictions"] and r["ns_event_locked_total"] >= r["ns_total"] and r["ns_event_lock_wait"] >= 0)
            chk(t + ":timing_matches_result", res["launches"][t]["timing_record"] == r)
        else:
            chk(t + ":timing_absent_off", recs == [] and "timing_record" not in res["launches"][t])
        chk(t + ":diag_line", "eventi_osservati=24672 righe_tentate=24672 righe_scritte=24672 righe_completate=24672" in err)
        trace_hash[t] = L["trace_sha256"]
    for a, b in (("A-8GiB-OFF", "A-8GiB-ON"), ("B-8GiB-OFF", "B-8GiB-ON")):
        chk(f"off_on_identity:{a}", trace_hash[a] == trace_hash[b] and res["pairs"][f"{a}~{b}"]["byte_identical"] and res["pairs"][f"{a}~{b}"]["diff_rows"] == 0)
    sv = res["sham_vs_b_selected"]
    chk("sham_vs_b", sv["ok"] and sv["diff_rows"] == 0 and sv["rows"] == 2 * EVENTS and "protected_before" not in sv["fields"])
    chk("sham_vs_b_trace_hashes_differ_as_expected", trace_hash["A-8GiB-OFF"] != trace_hash["B-8GiB-OFF"])
    if traces_dir:
        td = Path(traces_dir)
        for t in TAGS:
            p = td / f"{t}.trace.jsonl"
            chk(t + ":trace_sha256_rehash", sha(p) == trace_hash[t])
        for t in ("A-8GiB-OFF", "B-8GiB-OFF"):
            s = scan_trace(td / f"{t}.trace.jsonl")
            chk(t + ":independent_scan", s["policy_rows"] == s["completion_rows"] == EVENTS and s["unparsed"] == 0 and s["alternating"]
                and s["passes"] == PASSES and s["events_per_pass"] == [PER_PASS] and s["unique_events"] == EVENTS and s["budget_violations"] == 0, json.dumps(s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--traces-dir", default=None)
    a = ap.parse_args()
    r = run_checks(HERE / "host_records", a.traces_dir)
    bad = [c for c in r if not c["ok"]]
    print(json.dumps({"checks": len(r), "failed": [c["check"] for c in bad]}))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
