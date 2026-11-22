"""R3-05 rung R1: frozen event-stream extraction + replay fidelity. Model-free, additive, read-only on every source.

  python r1.py extract <label> <trace.jsonl> <out_dir>   stream one frozen R3-03A trace: sha256, canonical stream, meta (run in background; 5-10 GB)
  python r1.py finish  <work_dir>                        cross-trace equality, determinism, replay A/PP at 1.5 GiB, fidelity gate -> RESULT.json
  python r1.py build   <work_dir>                        (PASS only) write the additive evidence files + FREEZE.json
  python r1.py verify                                    offline re-verification of the committed evidence + freeze

Nothing here evaluates any candidate policy. The replay uses the FROZEN Python reference (tools/r2_12_real_trace_conformance, argine.research.policies).
"""
from __future__ import annotations

import argparse, hashlib, json, math, sys, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
P0 = HERE.parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tools"))

R3_03A_DIR = REPO / "experiments" / "R3-03A-runtime-semantic-conformance"
GATE2_METRICS = REPO / "experiments" / "R3-04-net-runtime-value" / "gate2-mechanism-evidence" / "MECHANISM_METRICS.json"
PHASE0_FREEZE_SHA = "415f78a850a2917a2457797106589433f944c07a863f80b14cef88fde6232ae3"
TOKEN_DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
N_LAYERS, EVENTS_PER_PASS, N_PASSES, N_EVENTS, N_DECODE = 48, 48, 514, 24672, 512
LABELS = {"A": ("A-4GiB", "b_selected", 4 * 2**30), "B": ("B-4GiB", "predicted_protect_sham", 4 * 2**30), "C": ("C-8GiB", "predicted_protect", 8 * 2**30)}
BUDGET_1_5 = 1536 * 2**20
# frozen 1.5 GiB runtime parameters (R2-09A B_selected(1.5 GiB); frozen predictor decay/top_k/n_experts)
PARAMS_1_5 = {"A": {"decay_factor": 0.999, "aging_interval_steps": 1, "predictor_decay": 0, "top_k": 0, "n_experts": 0},
              "PP": {"decay_factor": 0.999, "aging_interval_steps": 1, "predictor_decay": 0.99, "top_k": 8, "n_experts": 128}}
CANON_KEYS = ("i", "pass", "layer", "bytes", "req")


class R1Error(Exception):
    """Fail-closed R1 error (never repaired by changing the replay definition)."""


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def canon_line(i: int, p: int, l: int, b: int, req: List[int]) -> bytes:
    return (json.dumps({"i": i, "pass": p, "layer": l, "bytes": b, "req": req}, separators=(",", ":")) + "\n").encode("ascii")
def freeze_sha(o: Dict[str, Any]) -> str: return sha_bytes(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())


# ------------------------------------------------------------------ frozen source identity (read from the frozen R3-03A evidence, never typed)
def frozen_sources() -> Dict[str, Any]:
    man = json.loads((R3_03A_DIR / "EVIDENCE_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    res = json.loads((R3_03A_DIR / "CONFORMANCE_RESULT.json").read_text(encoding="utf-8"))
    out = {}
    for k, (cfg, pol, bud) in LABELS.items():
        la = res["launches"][cfg]; fm = man[f"{cfg}.trace.jsonl"]
        rec = json.loads((R3_03A_DIR / "host_records" / f"{cfg}.launch.json").read_text(encoding="utf-8"))
        if la["trace_sha256"] != fm["sha256"] or la["trace_bytes"] != fm["bytes"] or la["policy"] != pol or la["budget_bytes"] != bud:
            raise R1Error(f"frozen R3-03A records disagree for {cfg}")
        out[k] = {"config_id": cfg, "policy_id": pol, "budget_bytes": bud, "trace_sha256": fm["sha256"], "trace_bytes": fm["bytes"], "token_digest": rec["output_token_digest"],
                  "runtime_sha256": res["runtime"]["sha256"], "runtime_path": rec["runtime"]}
    return out


# ------------------------------------------------------------------ extraction (streaming; fail closed)
class Extractor:
    """Incremental, resumable, fail-closed extractor (state is JSON-serialisable so a long run can be split across short calls)."""

    def __init__(self, state: Optional[Dict[str, Any]] = None):
        st = state or {}
        self.n_ev = st.get("n_ev", 0); self.n_comp = st.get("n_comp", 0); self.rows = st.get("rows", 0); self.pending = st.get("pending")
        self.first = tuple(st["first"]) if st.get("first") else None; self.sizes = {int(k): v for k, v in st.get("sizes", {}).items()}
        self.cur_pass = st.get("cur_pass", 0); self.nxt = st.get("nxt", 0); self.passes = st.get("passes", 0)

    def state(self) -> Dict[str, Any]:
        return {"n_ev": self.n_ev, "n_comp": self.n_comp, "rows": self.rows, "pending": self.pending, "first": list(self.first) if self.first else None,
                "sizes": {str(k): v for k, v in self.sizes.items()}, "cur_pass": self.cur_pass, "nxt": self.nxt, "passes": self.passes}

    def feed(self, line: bytes) -> Optional[bytes]:
        """Returns the canonical line for a policy row, or None for a completion/blank row. Raises R1Error on any anomaly."""
        s = line.strip()
        if not s:
            return None
        self.rows += 1
        try:
            row = json.loads(s)
        except json.JSONDecodeError as e:
            raise R1Error(f"row {self.rows}: invalid JSON ({e})")
        if not isinstance(row, dict):
            raise R1Error(f"row {self.rows}: not an object")
        if row.get("complete") == 1 and "step" in row and "requested_experts" not in row:
            if set(row) != {"step", "complete", "cache_resident_bytes_post_service"}:
                raise R1Error(f"row {self.rows}: malformed completion row")
            if self.pending is None or row["step"] != self.pending:
                raise R1Error(f"row {self.rows}: completion step {row['step']} != pending {self.pending}")
            self.pending = None; self.n_comp += 1
            return None
        for k in ("step", "policy", "layer", "bytes_of_layer", "requested_experts", "budget_bytes", "policy_params", "semantic_event"):
            if k not in row:
                raise R1Error(f"row {self.rows}: missing field {k}")
        if self.pending is not None:
            raise R1Error(f"row {self.rows}: step {self.pending} has no completion row")
        if row["step"] != self.n_ev:
            raise R1Error(f"row {self.rows}: step {row['step']} != expected {self.n_ev}")
        se = row["semantic_event"]
        if se.get("schema") != "r3-03a-v1" or se.get("first_node") != 1 or se.get("layer") != row["layer"]:
            raise R1Error(f"step {self.n_ev}: invalid semantic_event {se}")
        ident = (row["policy"], json.dumps(row["policy_params"], sort_keys=True), row["budget_bytes"])
        if self.first is None:
            self.first = ident
        elif tuple(ident) != tuple(self.first):
            raise R1Error(f"step {self.n_ev}: policy/params/budget changed mid-trace")
        p, l = se["pass"], row["layer"]
        ok = (p == 0 and l == 0) if self.n_ev == 0 else ((p == self.cur_pass and l == self.nxt) if self.nxt < N_LAYERS else (p == self.cur_pass + 1 and l == 0))
        if not ok:
            raise R1Error(f"step {self.n_ev}: (pass={p}, layer={l}) out of order (expected pass {self.cur_pass}, layer {self.nxt})")
        if l == 0:
            self.passes += 1
        self.cur_pass, self.nxt = p, l + 1
        if self.sizes.setdefault(l, row["bytes_of_layer"]) != row["bytes_of_layer"]:
            raise R1Error(f"step {self.n_ev}: bytes_of_layer changed for layer {l}")
        req = row["requested_experts"]
        if not isinstance(req, list) or not all(type(e) is int and e >= 0 for e in req):
            raise R1Error(f"step {self.n_ev}: bad requested_experts")
        cl = canon_line(self.n_ev, p, l, row["bytes_of_layer"], req)
        self.pending = self.n_ev; self.n_ev += 1
        return cl

    def meta(self) -> Dict[str, Any]:
        if self.first is None:
            raise R1Error("empty trace")
        if self.pending is not None:
            raise R1Error(f"final step {self.pending} has no completion row")
        return {"policy_rows": self.n_ev, "completion_rows": self.n_comp, "passes": self.passes, "semantic_events": self.n_ev, "policy_id": self.first[0],
                "policy_params": json.loads(self.first[1]), "budget_bytes": self.first[2], "layer_sizes": {str(k): v for k, v in sorted(self.sizes.items())}}


def extract(lines: Iterable[bytes], out_path: Optional[Path] = None) -> Dict[str, Any]:
    """Stream a R3-03A JSONL trace (whole). Returns meta incl. sha256 of the raw bytes and of the canonical stream."""
    raw, can, ex = hashlib.sha256(), hashlib.sha256(), Extractor()
    fh = out_path.open("wb") if out_path else None
    try:
        for line in lines:
            raw.update(line)
            cl = ex.feed(line)
            if cl is not None:
                can.update(cl)
                if fh: fh.write(cl)
    finally:
        if fh: fh.close()
    m = ex.meta(); m["raw_sha256"] = raw.hexdigest(); m["canonical_stream_sha256"] = can.hexdigest()
    return m


def extract_chunk(trace: Path, out_path: Path, state_path: Path, max_seconds: float) -> bool:
    """Resumable extraction by byte offset. Returns True when the trace is fully consumed. The raw SHA-256 is computed separately (sha256sum)."""
    st = json.loads(state_path.read_text()) if state_path.exists() else {"offset": 0, "ext": {}}
    ex = Extractor(st["ext"]); off = st["offset"]; t0 = time.time()
    if out_path.exists() and out_path.stat().st_size != st.get("out_size", 0):
        raise R1Error("canonical output size does not match the checkpoint")
    done = False
    with trace.open("rb") as f, out_path.open("ab") as o:
        f.seek(off)
        while True:
            line = f.readline()
            if not line:
                done = True; break
            cl = ex.feed(line); off += len(line)
            if cl is not None: o.write(cl)
            if time.time() - t0 > max_seconds:
                break
        o.flush()
    st = {"offset": off, "ext": ex.state(), "out_size": out_path.stat().st_size, "done": done}
    state_path.write_text(json.dumps(st)); return done


def check_source_identity(meta: Dict[str, Any], frozen: Dict[str, Any], raw_bytes: int) -> List[str]:
    p = []
    if meta["raw_sha256"] != frozen["trace_sha256"]: p.append(f"trace sha256 {meta['raw_sha256']} != frozen {frozen['trace_sha256']}")
    if raw_bytes != frozen["trace_bytes"]: p.append(f"trace bytes {raw_bytes} != frozen {frozen['trace_bytes']}")
    if meta["policy_id"] != frozen["policy_id"]: p.append(f"policy {meta['policy_id']} != frozen {frozen['policy_id']}")
    if meta["budget_bytes"] != frozen["budget_bytes"]: p.append(f"budget {meta['budget_bytes']} != frozen {frozen['budget_bytes']}")
    if frozen["token_digest"] != TOKEN_DIGEST: p.append(f"frozen token digest {frozen['token_digest']} != {TOKEN_DIGEST}")
    if (meta["passes"], meta["semantic_events"]) != (N_PASSES, N_EVENTS): p.append(f"passes/events {meta['passes']}/{meta['semantic_events']} != {N_PASSES}/{N_EVENTS}")
    return p


def first_mismatch(a: List[bytes], b: List[bytes]) -> Optional[Dict[str, Any]]:
    """Compare two canonical streams (lists of lines). Reports the FIRST mismatch (index and both lines) or None."""
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None; y = b[i] if i < len(b) else None
        if x != y:
            return {"event_index": i, "a": x.decode() if x else None, "b": y.decode() if y else None}
    return None


# ------------------------------------------------------------------ replay (frozen Python reference only)
def replay(stream_lines: Iterable[bytes], policy_key: str, budget_bytes: int, params: Dict[str, Any]) -> Dict[str, Any]:
    import r2_12_real_trace_conformance as rtc
    from argine.research.policies import Context
    ev = [json.loads(l) for l in stream_lines]
    sizes = {e["layer"]: e["bytes"] for e in ev}
    pid = {"A": "b_selected", "PP": "predicted_protect"}[policy_key]
    ctx = Context(layers=sorted(sizes), n_experts=int(params["n_experts"]) or (max(max(e["req"], default=0) for e in ev) + 1), size=sizes, budget=budget_bytes, top_k=int(params.get("top_k") or 0))
    pol = rtc._build_policy(pid, params); pol.start(ctx)
    tot = {"misses": 0, "hits": 0, "miss_bytes": 0, "evictions": 0}; dec = {"misses": 0, "hits": 0, "miss_bytes": 0, "evictions": 0, "protected_events": 0, "protected_entries": 0}
    peak = 0; first_decode = 2 * EVENTS_PER_PASS
    for e in ev:
        prot = rtc._protected_for_layer(pol, pid, e["layer"])
        ev0 = pol.evictions
        missed = pol.access(e["i"], e["layer"], e["req"])
        req_u = sorted(set(e["req"])); mh = len(req_u) - len(missed); mb = len(missed) * e["bytes"]; evs = pol.evictions - ev0
        peak = max(peak, pol.resident_bytes)
        for d, cond in ((tot, True), (dec, e["i"] >= first_decode)):
            if cond:
                d["misses"] += len(missed); d["hits"] += mh; d["miss_bytes"] += mb; d["evictions"] += evs
        if e["i"] >= first_decode and prot:
            dec["protected_events"] += 1; dec["protected_entries"] += len(prot)
    return {"all_pass": tot, "decode": dec, "peak_policy_resident_bytes": peak, "overflow_events": pol.overflow_events, "overflow_misses": pol.overflow_misses, "events": len(ev)}


def expected_gate2(arm: str) -> Dict[str, Any]:
    m = json.loads(GATE2_METRICS.read_text(encoding="utf-8"))
    r = [p for p in m["per_launch"] if p["arm"] == arm and p["budget"] == "1.5" and p["policy_timing"] == "ON"][0]
    return r


def compare_gate2(rep: Dict[str, Any], exp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Integer-exact. Gate 2 stores per-token values = integer / 512 (exactly representable); we compare the integer numerators."""
    rows = []
    def chk(name, got, want):
        rows.append({"counter": name, "replay": got, "gate2": want, "exact": got == want})
    a, d = rep["all_pass"], rep["decode"]
    chk("all_pass_misses", a["misses"], exp["all_pass_misses"]); chk("all_pass_hits", a["hits"], exp["all_pass_hits"])
    chk("all_pass_evictions", a["evictions"], exp["all_pass_evictions"]); chk("all_pass_miss_bytes", a["miss_bytes"], exp["all_pass_miss_bytes"])
    for k in ("misses", "hits", "evictions", "miss_bytes"):
        want = exp[f"decode_{k}_per_token"] * N_DECODE
        chk(f"decode_{k}_x512", d[k], int(want) if float(want).is_integer() else want)
        chk(f"decode_{k}_per_token", d[k] / N_DECODE, exp[f"decode_{k}_per_token"])
    chk("decode_protected_events", d["protected_events"], exp["decode_protected_events"]); chk("decode_protected_entries", d["protected_entries"], exp["decode_protected_entries"])
    chk("resident_peak_bytes(policy)", rep["peak_policy_resident_bytes"], exp["resident_peak_bytes"])
    return rows


# ------------------------------------------------------------------ finish / build / verify
def read_lines(p: Path) -> List[bytes]: return p.read_bytes().splitlines(keepends=True)


def finish(work: Path) -> Dict[str, Any]:
    fr = frozen_sources(); metas, sizes = {}, {}
    for k in "ABC":
        metas[k] = json.loads((work / f"{k}.meta.json").read_text()); sizes[k] = metas[k]["raw_bytes"]
    res: Dict[str, Any] = {"artifact": "R3-05-R1-RESULT", "failures": []}
    for k in "ABC":
        pr = check_source_identity(metas[k], fr[k], sizes[k])
        if pr: res["failures"] += [f"source {k}: {x}" for x in pr]
    if res["failures"]:
        res["classification"] = "R3_05_R1_FIDELITY_FAIL"; return res
    s = {k: read_lines(work / f"{k}.stream.jsonl") for k in "ABC"}
    res["cross_trace"] = {}
    for x, y in (("A", "B"), ("A", "C")):
        mm = first_mismatch(s[x], s[y]); res["cross_trace"][f"{x}_vs_{y}"] = {"equal": mm is None, "first_mismatch": mm}
        if mm: res["failures"].append(f"cross-trace {x} vs {y}: first mismatch {mm}")
    res["extraction_deterministic"] = (work / "A.stream.jsonl").read_bytes() == (work / "A2.stream.jsonl").read_bytes()
    if not res["extraction_deterministic"]: res["failures"].append("two extractions of A differ")
    if res["failures"]:
        res["classification"] = "R3_05_R1_FIDELITY_FAIL"; return res
    res["canonical_stream_sha256"] = metas["A"]["canonical_stream_sha256"]
    res["replay"] = {}
    for key, arm in (("A", "A"), ("PP", "P")):
        rep = replay(s["A"], key, BUDGET_1_5, PARAMS_1_5[key]); rows = compare_gate2(rep, expected_gate2(arm))
        res["replay"][key] = {"replay": rep, "comparison": rows, "all_exact": all(r["exact"] for r in rows)}
        for r in rows:
            if not r["exact"]: res["failures"].append(f"{key}: {r['counter']} replay {r['replay']} != gate2 {r['gate2']}")
    res["classification"] = "R3_05_R1_FIDELITY_PASS" if not res["failures"] else "R3_05_R1_FIDELITY_FAIL"
    return res


EVIDENCE = ["README.md", "SOURCE_MANIFEST.json", "STREAM_MANIFEST.json", "FIDELITY_RESULTS.json", "CLAIM_BOUNDARIES.md", "CANONICAL_STREAM.jsonl", "r1.py", "tests/test_r1_core.py", "tests/test_r1_evidence.py"]
HOST_REL = "Argine4-r3runs/R3-03A-conformance-run2/{cfg}.trace.jsonl"


def build(work: Path) -> str:
    res = json.loads((work / "RESULT.json").read_text())
    if res["classification"] != "R3_05_R1_FIDELITY_PASS":
        raise R1Error("build is for PASS only")
    fr = frozen_sources(); metas = {k: json.loads((work / f"{k}.meta.json").read_text()) for k in "ABC"}
    src = {k: {"host_relative_path": HOST_REL.format(cfg=fr[k]["config_id"]), "config_id": fr[k]["config_id"], "policy_id": fr[k]["policy_id"], "budget_bytes": fr[k]["budget_bytes"],
               "trace_bytes": metas[k]["raw_bytes"], "trace_sha256": metas[k]["raw_sha256"], "token_digest": fr[k]["token_digest"], "runtime_sha256": fr[k]["runtime_sha256"],
               "passes": metas[k]["passes"], "semantic_events": metas[k]["semantic_events"], "completion_rows": metas[k]["completion_rows"],
               "bound_to": "experiments/R3-03A-runtime-semantic-conformance/EVIDENCE_MANIFEST.json + host_records/*.launch.json + CONFORMANCE_RESULT.json (frozen R3-03A)",
               "primary_extraction_source": k == "A", "raw_hash_method": "sha256sum over the full file, read-only"} for k in "ABC"}
    stream = (work / "A.stream.jsonl").read_bytes()
    sm = {"artifact": "R3-05-R1-STREAM-MANIFEST", "canonical_stream_file": "CANONICAL_STREAM.jsonl", "canonical_stream_sha256": sha_bytes(stream), "bytes": len(stream), "events": N_EVENTS, "passes": N_PASSES,
          "events_per_pass": EVENTS_PER_PASS, "decode_first_event_index": 2 * EVENTS_PER_PASS, "layer_sizes_bytes": metas["A"]["layer_sizes"], "line_format": "{\"i\",\"pass\",\"layer\",\"bytes\",\"req\"} compact JSON, key order fixed, \\n terminated",
          "excluded": "all policy-dependent and timing fields (hit/miss/evicted/resident sets, scores, protected sets, residency bytes, policy id/params)",
          "equality_rule": "per-event (i, pass, layer, bytes, req) byte-identical across A-4GiB, B-4GiB, C-8GiB (Phase 0: only (pass, layer, bytes_of_layer, requested_experts) extracted)",
          "cross_trace": res["cross_trace"], "extraction_byte_deterministic": res["extraction_deterministic"],
          "determinism_method": "A extracted twice from the same source (independent resumable runs); outputs byte-identical"}
    out = {"SOURCE_MANIFEST.json": dumps({"artifact": "R3-05-R1-SOURCE-MANIFEST", "sources": src, "phase0_freeze_sha256": PHASE0_FREEZE_SHA, "note": "absolute host paths are intentionally not committed; source sizes and SHA-256 bind the files"}),
           "STREAM_MANIFEST.json": dumps(sm), "FIDELITY_RESULTS.json": dumps(res)}
    for n, t in out.items(): (HERE / n).write_text(t, encoding="utf-8")
    (HERE / "CANONICAL_STREAM.jsonl").write_bytes(stream)
    fz = {"artifact": "R3-05-R1-FREEZE", "classification": res["classification"], "files_sha256": {n: sha_bytes((HERE / n).read_bytes()) for n in EVIDENCE}, "phase0_freeze_sha256": PHASE0_FREEZE_SHA,
          "canonical_stream_sha256": sm["canonical_stream_sha256"], "authorizes": "Nothing candidate-related. Permits R2 (clairvoyant ceiling) and R3 (CES / PP_CURVE) to consume CANONICAL_STREAM.jsonl; each needs its own prompt."}
    fz["freeze_sha256"] = freeze_sha(fz); (HERE / "FREEZE.json").write_text(dumps(fz), encoding="utf-8"); return fz["freeze_sha256"]


def verify(replay_too: bool = True) -> List[str]:
    p: List[str] = []
    import importlib.util
    spec = importlib.util.spec_from_file_location("_p0", P0 / "phase0.py"); z = importlib.util.module_from_spec(spec); spec.loader.exec_module(z)   # type: ignore
    if z.verify(): p.append("Phase 0 verification failed")
    if json.loads((P0 / "FREEZE.json").read_text())["freeze_sha256"] != PHASE0_FREEZE_SHA: p.append("Phase 0 freeze sha changed")
    fz = json.loads((HERE / "FREEZE.json").read_text())
    if fz["freeze_sha256"] != freeze_sha(fz): p.append("R1 freeze sha mismatch")
    for n, h in fz["files_sha256"].items():
        if sha_bytes((HERE / n).read_bytes()) != h: p.append(f"{n} differs from freeze")
    txt = "".join((HERE / n).read_text(encoding="utf-8") for n in ("SOURCE_MANIFEST.json", "STREAM_MANIFEST.json", "FIDELITY_RESULTS.json", "README.md"))
    if "alpel" in txt or "C:\\" in txt: p.append("host path leaked")
    sm = json.loads((HERE / "STREAM_MANIFEST.json").read_text()); data = (HERE / "CANONICAL_STREAM.jsonl").read_bytes()
    if sha_bytes(data) != sm["canonical_stream_sha256"]: p.append("canonical stream hash mismatch")
    lines = data.splitlines(keepends=True)
    if len(lines) != N_EVENTS: p.append("event count")
    passes = 0
    for i, l in enumerate(lines):
        e = json.loads(l)
        if list(e) != list(CANON_KEYS) or e["i"] != i or e["layer"] != i % N_LAYERS or e["pass"] != i // N_LAYERS or l != canon_line(e["i"], e["pass"], e["layer"], e["bytes"], e["req"]): p.append(f"stream line {i} malformed"); break
        passes += e["layer"] == 0
    if passes != N_PASSES: p.append("pass count")
    src = json.loads((HERE / "SOURCE_MANIFEST.json").read_text())["sources"]; frz = frozen_sources()
    for k in "ABC":
        for f, g in (("trace_sha256", "trace_sha256"), ("trace_bytes", "trace_bytes"), ("token_digest", "token_digest"), ("policy_id", "policy_id"), ("budget_bytes", "budget_bytes"), ("runtime_sha256", "runtime_sha256")):
            if src[k][f] != frz[k][g]: p.append(f"source {k} {f} differs from frozen R3-03A evidence")
        if src[k]["token_digest"] != TOKEN_DIGEST or (src[k]["passes"], src[k]["semantic_events"]) != (N_PASSES, N_EVENTS): p.append(f"source {k} identity")
    res = json.loads((HERE / "FIDELITY_RESULTS.json").read_text())
    if res["classification"] != "R3_05_R1_FIDELITY_PASS" or res["failures"]: p.append("recorded classification is not PASS")
    if not all(v["equal"] for v in res["cross_trace"].values()) or not res["extraction_deterministic"]: p.append("recorded cross-trace/determinism not PASS")
    if replay_too:
        for key, arm in (("A", "A"), ("PP", "P")):
            rep = replay(lines, key, BUDGET_1_5, PARAMS_1_5[key]); rows = compare_gate2(rep, expected_gate2(arm))
            for r in rows:
                if not r["exact"]: p.append(f"{key} {r['counter']} {r['replay']} != {r['gate2']}")
            if rep != res["replay"][key]["replay"]: p.append(f"{key} replay differs from recorded result")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*"); a = ap.parse_args()
    if a.cmd == "extract":
        label, trace, out = a.args[0], Path(a.args[1]), Path(a.args[2]); out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        with trace.open("rb") as f:
            meta = extract(f, out / f"{label}.stream.jsonl")
        meta["raw_bytes"] = trace.stat().st_size; meta["seconds"] = round(time.time() - t0, 1)
        (out / f"{label}.meta.json").write_text(dumps(meta)); print(label, "done", meta["canonical_stream_sha256"], meta["seconds"])
    elif a.cmd == "chunk":
        label, trace, out, secs = a.args[0], Path(a.args[1]), Path(a.args[2]), float(a.args[3])
        d = extract_chunk(trace, out / f"{label}.stream.jsonl", out / f"{label}.state.json", secs); print(label, "DONE" if d else "partial", json.loads((out / f"{label}.state.json").read_text())["ext"]["n_ev"])
    elif a.cmd == "meta":
        w, trace_dir = Path(a.args[0]), Path(a.args[1])
        for label, cfg in (("A", "A-4GiB"), ("B", "B-4GiB"), ("C", "C-8GiB")):
            st = json.loads((w / f"{label}.state.json").read_text()); assert st["done"]
            ex = Extractor(st["ext"]); m = ex.meta()
            m["raw_sha256"] = (w / f"{cfg}.sha256").read_text().split()[0]; m["raw_bytes"] = (trace_dir / f"{cfg}.trace.jsonl").stat().st_size
            m["canonical_stream_sha256"] = sha_bytes((w / f"{label}.stream.jsonl").read_bytes()); (w / f"{label}.meta.json").write_text(dumps(m)); print(label, m["canonical_stream_sha256"])
    elif a.cmd == "build":
        print("freeze", build(Path(a.args[0])))
    elif a.cmd == "verify":
        pr = verify(); print("VERIFY", "OK" if not pr else pr); sys.exit(1 if pr else 0)
    elif a.cmd == "finish":
        w = Path(a.args[0]); r = finish(w); (w / "RESULT.json").write_text(dumps(r)); print(r["classification"]); print("\n".join(r["failures"][:10]))
