"""Research 4 Phase 1 -- CFDC Training and Prospective Validation (orchestrator).

  gate        first gate: Phase 0 verify + git state            fidelity   replay-fidelity gate -> REPLAY_FIDELITY.json
  labels      TRAIN teacher labels -> manifests                  fit-cheap  fit the frozen CHEAP tree (TRAIN only)
  fit-rich    fit the frozen RICH GBRT (TRAIN only)              pack       hashes, plan, implementation record (pre-validation)
  open        write VALIDATION_OPENED.json (hash-only; separate commit)
  score       score VALIDATION exactly once (refuses unless the opening record is committed) -> VALIDATION_RESULTS/DECISION
  freeze      PROVENANCE.json + FREEZE.json                      verify     re-check everything present
No EVAL loader exists in this module; EVAL stays LOCKED.
"""
from __future__ import annotations

import argparse, hashlib, json, os, platform, re, subprocess, sys, time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "tools")); sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import engine as G  # noqa: E402
import models as M  # noqa: E402

P0 = REPO / "experiments" / "R4-00-causal-eviction-distillation"
R3 = REPO / "experiments" / "R3-05-economic-retention"
WORK = Path(os.environ.get("R4_WORK", str(Path.home() / "r4work")))

STATE_A = "R4_CFDC_PHASE1_TRAINED_AND_FROZEN"
STATE_FINAL = "R4_CFDC_PHASE1_VALIDATED"
EVAL_STATE = "LOCKED"
PHASE0_COMMIT = "35410947d51895326aa7e6c39a09825e2ffb4c0c"
PHASE0_DIGEST = "fa0ff8d4c93b463a99bdba4a65625251fa00c8102f5b2d788f8f22e73a20d64e"
PHASE0_FREEZE = "41e9aa82fcf59155b6c98504ff08bee6ca2feb74027675e3fbce10778ca318be"
SOURCE_R3_HEAD = "f88001558542b648c0a3abff54c919adedf27809"
BRANCH = "r4-main"
CANON_STREAM_SHA = "24fcc3e31e800826acb353c627326d4665af7b05c88a3138b18b79c5c435e776"
FIRST_DECODE, N_DECODE = 96, 512
KAPPA_LOW = 0.3792  # ms/MB (Research 3 Phase 0 transfer mapping)
IMPL_FILES = ["engine.py", "models.py", "_rich_infer.c", "phase1.py"]
ARTIFACTS = ["README.md", "IMPLEMENTATION.json", "REPLAY_FIDELITY.json", "TRAIN_MANIFEST.json", "TEACHER_MANIFEST.json", "FEATURE_IMPLEMENTATION.json", "RICH_MODEL.json", "CHEAP_MODEL.json",
             "CHEAP_RULE.md", "MODEL_HASHES.json", "VALIDATION_PLAN.json"]
POST = ["VALIDATION_OPENED.json", "VALIDATION_RESULTS.json", "VALIDATION_DECISION.json", "PROVENANCE.json"]
TESTS = ["tests/test_phase1.py"]


class P1Error(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def lf_sha(p: Path) -> str: return sha_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
def canon(o: Any) -> bytes: return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
def jread(p: Path) -> Any: return json.loads(p.read_text(encoding="utf-8"))
def jwrite(name: str, o: Any) -> None: (HERE / name).write_text(dumps(o), encoding="utf-8", newline="\n")
def git(*a: str) -> str: return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=True).stdout.strip()
def freeze_sha(o: Dict[str, Any]) -> str: return sha_bytes(canon({k: v for k, v in o.items() if k != "freeze_sha256"}))
def sanitize(p: str) -> str:
    m = re.search(r"[\\/]research[\\/](.*)$", p); return "%LOCALAPPDATA%\\Argine3\\research\\" + m.group(1).replace("/", "\\") if m else p


# ====================================================================== frozen inputs
def splits() -> Dict[str, Any]: return jread(P0 / "DATA_SPLITS.json")["splits"]
ALLOWED_SPLITS = ("TRAIN", "VALIDATION", "REPLICATE_CHECK")


class EvalLocked(Exception):
    pass


def resolve_trace(entry: Dict[str, Any]) -> Path:
    m = re.search(r"[\\/]research[\\/]([^\\/]+)[\\/](.*)$", entry["trace_path_host"]); top, rest = m.group(1), m.group(2).replace("\\", "/")
    roots = [Path(os.environ["ARGINE_RESEARCH_ROOT"]) / top] if os.environ.get("ARGINE_RESEARCH_ROOT") else []
    roots += [Path.home() / "mnt" / top]
    if os.environ.get("LOCALAPPDATA"): roots.append(Path(os.environ["LOCALAPPDATA"]) / "Argine3" / "research" / top)
    for r in roots:
        if (r / rest).exists(): return r / rest
    raise P1Error(f"trace {entry.get('instance_id') or entry.get('workload_id')} unavailable (missing prerequisite)")


def trace_hash(split: str, entry: Dict[str, Any]) -> str:
    if split not in ALLOWED_SPLITS: raise EvalLocked("EVAL is LOCKED in Phase 1")
    return sha_bytes(resolve_trace(entry).read_bytes())


def load_trace(split: str, entry: Dict[str, Any]):
    """Hash-verified load. Only TRAIN, VALIDATION and REPLICATE_CHECK entries are loadable; any other split raises EvalLocked."""
    if split not in ALLOWED_SPLITS: raise EvalLocked("EVAL is LOCKED in Phase 1")
    if "eval" in (str(entry.get("workload_id", "")) + str(entry.get("trace_path_host", ""))).lower(): raise EvalLocked("EVAL trace refused")
    p = resolve_trace(entry)
    if sha_bytes(p.read_bytes()) != entry["trace_sha256"]: raise P1Error(f"trace sha256 differs from DATA_SPLITS: {entry.get('instance_id') or entry.get('workload_id')}")
    from argine.research.trace import Trace
    return Trace.load(str(p))


def ident(split: str, e: Dict[str, Any]) -> str: return e.get("instance_id") or e["workload_id"]


# ====================================================================== gate
def gate() -> Dict[str, Any]:
    r = subprocess.run([sys.executable, str(P0 / "phase0.py"), "verify"], capture_output=True, text=True)
    fz = jread(P0 / "FREEZE.json")
    out = {"phase0_verify_ok": r.returncode == 0 and "VERIFY OK" in r.stdout, "phase0_verify_output": r.stdout.strip().splitlines()[-1:] if r.stdout else r.stderr[-200:],
           "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"), "status_short": git("status", "--short"), "phase0_contract_digest": fz.get("contract_digest"), "phase0_freeze_sha256": fz.get("freeze_sha256"),
           "phase0_state": fz.get("state"), "eval_state": fz.get("eval_state")}
    out["ok"] = bool(out["phase0_verify_ok"] and out["branch"] == BRANCH and out["phase0_contract_digest"] == PHASE0_DIGEST and out["phase0_freeze_sha256"] == PHASE0_FREEZE and out["eval_state"] == "LOCKED")
    return out


# ====================================================================== fidelity
def _cmp(actual: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, Any]:
    keys = sorted(expected); return {"exact_equal": all(actual.get(k) == expected[k] for k in keys), "compared_fields": keys, "expected": {k: expected[k] for k in keys}, "actual": {k: actual.get(k) for k in keys}}


def _cache(name: str):
    d = WORK / "cache"; d.mkdir(parents=True, exist_ok=True); return d / (name.replace("/", "_").replace(":", "_") + ".json")


def fidelity(include_validation_a: bool = True, budget_s: float = 1e9) -> Dict[str, Any]:
    """Resumable: every check is a unit whose result is cached in the scratch directory; returns {"incomplete": True} when the time budget is exhausted."""
    t0 = time.time(); checks: List[Dict[str, Any]] = []; keys = ("misses", "hits", "miss_bytes", "evictions")
    units: List[Any] = []
    cs = R3 / "r1-fidelity" / "CANONICAL_STREAM.jsonl"; data = cs.read_bytes()
    if sha_bytes(data) != CANON_STREAM_SHA: raise P1Error("canonical stream sha256 differs")
    holder: Dict[str, Any] = {}
    def canon_es():
        if "es" not in holder: holder["es"] = G.from_canonical([json.loads(l) for l in data.splitlines()], FIRST_DECODE, N_DECODE)
        return holder["es"]
    r2 = jread(R3 / "r2-belady-ceiling" / "RESULTS.json")["budgets"]["1.5"]; r3x = jread(R3 / "r4-nue-candidate-gate" / "EVAL_RESULTS.json")["raw"]["NUE_X"]["decode"]
    for nm, run, exp, src in (("A", lambda: G.Sim(canon_es(), G.PolA()).run(), r2["A"]["decode"], "R3-05 r2-belady-ceiling/RESULTS.json"), ("BELADY", lambda: G.run_belady(canon_es()), r2["BELADY"]["decode"], "R3-05 r2-belady-ceiling/RESULTS.json"),
                              ("NUE_X", lambda: G.Sim(canon_es(), G.PolNUEX()).run(), r3x, "R3-05 r4-nue-candidate-gate/EVAL_RESULTS.json raw.NUE_X")):
        def unit(nm=nm, run=run, exp=exp, src=src):
            t = time.time(); res = run(); c = _cmp(res["decode"], {k: exp[k] for k in keys})
            c.update({"stream": "research canonical decode stream (24,672 events, 1.5 GiB)", "policy": nm, "budget_violations": res["budget_violations"], "frozen_source": src}); return c
        units.append((f"canon_{nm}", unit))
    dev = jread(R3 / "r4-nue-candidate-gate" / "DEV_RESULTS.json")["runs"]
    for e in splits()["TRAIN"]["traces"]:
        for nm, pol, arm in (("A", G.PolA, "A"), ("NUE_X", G.PolNUEX, "NUE_X:LEX")):
            def unit(e=e, nm=nm, pol=pol, arm=arm):
                es_t = G.from_trace(load_trace("TRAIN", e), G.BUDGET, ident("TRAIN", e)); res = G.Sim(es_t, pol()).run(); exp = dev[e["instance_id"]][arm]
                c = _cmp({**res["decode"], "evictions_total": res["evictions_total"], "peak_resident_bytes": res["peak_resident_bytes"]}, {**{k: exp["decode"][k] for k in ("misses", "hits", "miss_bytes", "accesses", "events", "tokens")}, "evictions_total": exp["evictions_total"], "peak_resident_bytes": exp["peak_resident_bytes"]})
                c.update({"stream": f"TRAIN {e['instance_id']}", "policy": nm, "budget_violations": res["budget_violations"], "frozen_source": f"R3-05 r4-nue-candidate-gate/DEV_RESULTS.json runs[{e['instance_id']}].{arm}"}); return c
            units.append((f"train_{e['instance_id']}_{nm}", unit))
    if include_validation_a:
        for e in splits()["VALIDATION"]["traces"]:
            def unit(e=e):
                es_v = G.from_trace(load_trace("VALIDATION", e), G.BUDGET, e["workload_id"]); res = G.Sim(es_v, G.PolA()).run(); fa = e["frozen_A_reference"]
                c = _cmp({"misses": res["decode"]["misses"], "miss_bytes": res["decode"]["miss_bytes"], "tokens": res["decode"]["tokens"]}, {"misses": fa["decode_misses"], "miss_bytes": fa["decode_bytes_missed"], "tokens": fa["decode_steps"]})
                c.update({"stream": f"VALIDATION {e['workload_id']}", "policy": "A", "budget_violations": res["budget_violations"], "frozen_source": fa["source"], "note": "reproduction of the frozen R2-11 reference only"}); return c
            units.append((f"val_{e['workload_id']}_A", unit))
    for key, unit in units:
        f = _cache("fid_" + key)
        if not f.exists():
            if time.time() - t0 > budget_s: return {"incomplete": True, "done": sum(_cache("fid_" + k).exists() for k, _ in units), "total": len(units)}
            ts = time.time(); c = unit(); c["seconds"] = round(time.time() - ts, 1); f.write_text(dumps(c))
        checks.append(jread(f))
    ok = all(c["exact_equal"] and c["budget_violations"] == 0 for c in checks)
    return {"artifact": "R4-01-REPLAY-FIDELITY", "criterion": "exact integer equality with the frozen historical values on the streams where they were produced; no tolerance is needed (all quantities are integers)", "belady_note": "BELADY is the frozen argine.research.oracle.BeladyGlobal class driven through the Phase 1 event-list adapter (the same adapter pattern as R3-05 r2.py); its frozen reference exists on the Research canonical stream only",
            "nue_x_note": "NUE_X uses the frozen R2-10 DecayedTransitionPredictor and the frozen R3 key; its historical 8.79% is not compared to any Research 4 split", "checks": checks, "all_exact": ok, "n_checks": len(checks)}


# ====================================================================== labels / fits
def _feature_schema_hash() -> str: return sha_bytes(canon({"feature_order": G.FEATURE_ORDER, "contract_sha256_lf": lf_sha(P0 / "FEATURE_CONTRACT.json")}))


def labels() -> Dict[str, Any]:
    WORK.mkdir(exist_ok=True); per = []; sh = _feature_schema_hash()
    dev = jread(R3 / "r4-nue-candidate-gate" / "DEV_RESULTS.json")["runs"]
    for ti, e in enumerate(splits()["TRAIN"]["traces"]):
        es = G.from_trace(load_trace("TRAIN", e), G.BUDGET, ident("TRAIN", e)); t = time.time(); out = G.teacher_rows(es)
        if out["replay"]["decode"]["miss_bytes"] != dev[e["instance_id"]]["A"]["decode"]["miss_bytes"]: raise P1Error("A trajectory differs from the frozen DEV row")
        np.savez(WORK / f"train_{ti}.npz", X=out["X"], y=out["y"], g=out["g"])
        per.append({"trace": e["instance_id"], "source_trace_sha256": e["trace_sha256"], "served_events": es.n, "a_eviction_decisions": out["replay"]["decisions"], "sampled_decisions": out["stats"]["sampled_decisions"], "candidate_rows": out["stats"]["rows"],
                    "shield_total_fallback_sampled": out["stats"]["fallback_decisions"], "never_used_again_rows": out["stats"]["inf_rows"], "a_victim_equals_teacher_max_sampled": out["stats"]["a_victim_is_teacher_max"],
                    "feature_schema_hash": sh, "teacher_label_sha256": sha_bytes(out["y"].astype("<f8").tobytes()), "feature_matrix_sha256": sha_bytes(out["X"].astype("<i4").tobytes()), "decision_ids_sha256": sha_bytes(out["g"].astype("<i8").tobytes()),
                    "a_replay_decode_miss_bytes": out["replay"]["decode"]["miss_bytes"], "seconds": round(time.time() - t, 1)})
    return {"per_trace": per, "feature_schema_hash": sh}


def load_train():
    Xs, ys, gs = [], [], []
    for ti in range(4):
        z = np.load(WORK / f"train_{ti}.npz"); Xs.append(z["X"]); ys.append(z["y"]); gs.append(z["g"] + ti * 10_000_000)
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(gs)


def fit_cheap_cmd() -> Dict[str, Any]:
    X, y, g = load_train(); tree = M.fit_cheap(X, y, g)
    # leaf-support audit and integer audit
    return tree


# ====================================================================== validation scoring
def pooled(arms: Dict[str, Dict[str, int]], ids: List[str], pol: str) -> Dict[str, Any]:
    ba = sum(arms[i]["A"] for i in ids); bb = sum(arms[i]["BELADY"] for i in ids); bp = sum(arms[i][pol] for i in ids)
    den = ba - bb
    return {"B_A": ba, "B_BELADY": bb, "B_P": bp, "denominator": den, "avoided_vs_A": ba - bp, "capture": (str(Fraction(ba - bp, den)) if den > 0 else None), "capture_float": (float(Fraction(ba - bp, den)) if den > 0 else None)}


def score_trace(e: Dict[str, Any]) -> Dict[str, Any]:
    cheap = jread(HERE / "CHEAP_MODEL.json"); rich = M.RichModel.from_json(jread(HERE / "RICH_MODEL.json"))
    es = G.from_trace(load_trace("VALIDATION", e), G.BUDGET, e["workload_id"]); out = {"workload_id": e["workload_id"], "category": e["nominal_category"], "trace_sha256": e["trace_sha256"]}
    runs = {"A": lambda: G.Sim(es, G.PolA()).run(), "BELADY": lambda: G.run_belady(es), "NUE_X": lambda: G.Sim(es, G.PolNUEX()).run(), "DECAYED_COUNT": lambda: G.Sim(es, G.PolDC()).run(),
            "CHEAP": lambda: G.Sim(es, G.PolCheap(cheap["tree"])).run(), "RICH": lambda: G.Sim(es, G.PolRich(rich)).run()}
    for nm, f in runs.items():
        t = time.time(); r = f(); r["seconds"] = round(time.time() - t, 1); r["backend"] = rich.backend if nm == "RICH" else "numpy"; r["result_sha256"] = sha_bytes(canon({k: v for k, v in r.items() if k not in ("seconds", "backend")})); out[nm] = r
    return out


def score_all(budget_s: float = 1e9) -> Any:
    """Resumable: each trace is scored once and cached in the scratch directory; returns None while traces remain."""
    import multiprocessing as mp
    t0 = time.time(); entries = splits()["VALIDATION"]["traces"]
    pending = [e for e in entries if not _cache("val_" + e["workload_id"]).exists()]
    if pending:
        M.RichModel.from_json(jread(HERE / "RICH_MODEL.json"))._load_lib()       # compile once before forking
        last = 0.0
        while pending and (last == 0.0 or time.time() - t0 + 1.3 * last < budget_s):
            batch, pending = pending[:2], pending[2:]; ts = time.time()
            with mp.Pool(len(batch)) as pool: res = pool.map(score_trace, batch, chunksize=1)
            for e, r in zip(batch, res): _cache("val_" + e["workload_id"]).write_text(dumps(r))
            last = time.time() - ts
    if pending: return None
    return assemble([jread(_cache("val_" + e["workload_id"])) for e in entries])


def assemble(per: List[Dict[str, Any]]) -> Dict[str, Any]:
    pols = ["A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"]; ids = [p["workload_id"] for p in per]
    arms = {p["workload_id"]: {k: p[k]["decode"]["miss_bytes"] for k in pols} for p in per}
    tokens = {p["workload_id"]: p["A"]["decode"]["tokens"] for p in per}
    integrity = {"A_reproduces_frozen_reference_all_12": True, "budget_violations": sum(p[k]["budget_violations"] for p in per for k in pols), "pooled_denominator_positive": sum(arms[i]["A"] - arms[i]["BELADY"] for i in ids) > 0}
    for p, e in zip(per, splits()["VALIDATION"]["traces"]):
        fa = e["frozen_A_reference"]
        if (p["A"]["decode"]["misses"], p["A"]["decode"]["miss_bytes"]) != (fa["decode_misses"], fa["decode_bytes_missed"]): integrity["A_reproduces_frozen_reference_all_12"] = False
    integrity["ok"] = integrity["A_reproduces_frozen_reference_all_12"] and integrity["budget_violations"] == 0 and integrity["pooled_denominator_positive"]
    ex = [i for i in ids if i != "r2-11-coding-v1"]
    agg = {pol: pooled(arms, ids, pol) for pol in pols if pol not in ("A", "BELADY")}
    sens = {pol: pooled(arms, ex, pol) for pol in pols if pol not in ("A", "BELADY")}
    cats = sorted({p["category"] for p in per})
    perw: Dict[str, Any] = {}
    for p in per:
        i = p["workload_id"]; den = arms[i]["A"] - arms[i]["BELADY"]; row = {"category": p["category"], "B_A": arms[i]["A"], "B_BELADY": arms[i]["BELADY"], "denominator": den}
        for pol in pols[2:]:
            d = arms[i]["A"] - arms[i][pol]; row[pol] = {"B_P": arms[i][pol], "avoided_vs_A": d, "capture": (str(Fraction(d, den)) if den > 0 else None), "capture_float": (float(Fraction(d, den)) if den > 0 else None), "sign": ("+" if d > 0 else "-" if d < 0 else "0"),
                                                         "zero_cost_ms_per_token": KAPPA_LOW * d / tokens[i] / 1e6,
                                                         "misses_per_token": p[pol]["decode"]["misses"] / tokens[i], "miss_bytes_per_token": p[pol]["decode"]["miss_bytes"] / tokens[i], "decode_evictions": p[pol]["decode"]["evictions"], "decode_reloads": p[pol]["decode_reloads"],
                                                         "decode_misses_per_layer": p[pol]["decode_misses_per_layer"], "turnover_evictions_per_token": p[pol]["decode"]["evictions"] / tokens[i], "result_sha256": p[pol]["result_sha256"]}
        row["A_result_sha256"] = p["A"]["result_sha256"]; row["BELADY_result_sha256"] = p["BELADY"]["result_sha256"]; perw[i] = row
    percat = {c: {pol: pooled(arms, [i for i in ids if perw[i]["category"] == c], pol) for pol in pols[2:]} for c in cats}
    cheap = agg["CHEAP"]; meets = bool(integrity["ok"] and cheap["capture"] is not None and Fraction(cheap["capture"]) >= Fraction(1, 4))
    results = {"artifact": "R4-01-VALIDATION-RESULTS", "split": "VALIDATION (12 R2-11 traces)", "budget_bytes": G.BUDGET, "endpoint": "capture(P) = sum(B_A - B_P) / sum(B_A - B_BELADY), decode miss bytes at 1.5 GiB, pooled, exact rational, uncapped",
               "integrity": integrity, "aggregate_all_12": agg, "sensitivity_excluding_r2-11-coding-v1_NONGATING": sens, "per_category": percat, "per_workload": perw, "B_A_total": sum(arms[i]["A"] for i in ids), "B_BELADY_total": sum(arms[i]["BELADY"] for i in ids),
               "tokens_total": sum(tokens.values()), "replicate_check": "not scored in Phase 1 (report-only; Phase 2 may report it)", "seconds_per_run": {p["workload_id"]: {k: p[k]["seconds"] for k in pols} for p in per}}
    decision = {"artifact": "R4-01-VALIDATION-DECISION", "rule": "capture_VAL(CHEAP) >= 1/4 (DECISION_RULES.rules.A, VALIDATION half); 24.999% remains below the threshold", "cheap_capture_exact": cheap["capture"], "cheap_capture_float": cheap["capture_float"],
                "cheap_meets_validation_gate": meets, "rich_capture_float": agg["RICH"]["capture_float"], "rich_meets_validation_half_of_rich_gate": bool(agg["RICH"]["capture"] is not None and Fraction(agg["RICH"]["capture"]) >= Fraction(1, 4)),
                "nue_x_capture_float": agg["NUE_X"]["capture_float"], "decayed_count_capture_float": agg["DECAYED_COUNT"]["capture_float"], "integrity_ok": integrity["ok"],
                "validation_overlap_sensitivity_nongating": {pol: sens[pol]["capture_float"] for pol in sens}, "per_workload_cheap_sign": {i: perw[i]["CHEAP"]["sign"] for i in ids},
                "final_classification": "NOT COMPUTED: requires EVAL (Phase 2)", "policy_changed_after_validation": False, "eval_state": EVAL_STATE,
                "authorizes": "Nothing by itself. EVAL is opened exactly once in Phase 2 whatever this result is (DECISION_RULES.eval_opening)."}
    return {"results": results, "decision": decision}


# ====================================================================== pack / open / freeze / verify
def impl_record() -> Dict[str, Any]:
    return {"artifact": "R4-01-IMPLEMENTATION", "files_lf_sha256": {f: lf_sha(HERE / f) for f in IMPL_FILES}, "test_files_lf_sha256": {f: lf_sha(HERE / f) for f in TESTS if (HERE / f).exists()}, "python": sys.version.split()[0], "numpy": np.__version__, "platform": platform.platform(),
            "dependencies": "numpy only (no new production dependency); optional C inference helper _rich_infer.c with a bit-identical numpy fallback", "base_commit": PHASE0_COMMIT, "phase0_contract_digest": PHASE0_DIGEST,
            "determinism": "single-thread numpy; no randomness is used by CART or GBRT (random_seed 0 is recorded; there is no row/feature subsampling)", "rounding": "predictions quantized with round-half-even of pred*2^16",
            "independent_review": REVIEW, "pow_table_sha256": sha_bytes(np.array([G.DECAY ** n for n in range(1024)], dtype="<f8").tobytes()), "interpretations": INTERPRETATIONS}


REVIEW = {"method": "read-only review agent compared engine.py / models.py / _rich_infer.c with the frozen Phase 0 contracts and brute-force reimplementations (3,449 sampled decisions, 200 random CHEAP split cases)", "confirmed_defects": 0,
          "minor_observations": ["float64 noise can create a split on a constant non-integer y node (gain ~1e-12); disclosed, not changed because CHEAP was already fitted",
                                 "RICH bin grid = quantiles k/256, k=1..255, de-duplicated (a reading of '255 quantile cut points')",
                                 "hit/miss interleaving inside an event follows the frozen policies (ascending expert id); it matters only in the total-fallback case",
                                 "C-to-numpy fallback was silent; now warns and the scorer records the RICH backend (arithmetic identical, tested)"]}

INTERPRETATIONS = [
    "Candidate rows of TRAIN decisions are the shield-with-total-fallback set computed at A's state, although A itself evicts without a shield (frozen: teacher trajectory = A states; candidate set = FEATURE_CONTRACT).",
    "pending_misses counts the current missed expert plus later-id members of the current event that are not resident at the decision.",
    "In the rare total-fallback case a candidate may be a member of the current event: its features reflect requests processed so far, except decayed_count_fx/S which exclude the current event's request (frozen).",
    "Decision numbering counts every A eviction (prefill included) from 0; decisions with index mod 64 == 0 are sampled.",
    "F02 uses a precomputed table 0.99**n (Python float pow); the same table serves DECAYED_COUNT (exact binary64 S) and decayed_count_fx = floor(65536*S).",
    "RICH bins: <=255 distinct values -> every value is a cut; otherwise the unique values of the 255 'lower' quantile cut points; split rule x <= cut goes left; level-wise growth; leaf = mean residual; split gain must be > 0 and each child >= 200 rows.",
    "CHEAP gains and ties are compared in float64 (exact equality), then lower feature index, then lower threshold.",
]


def pack_plan() -> Dict[str, Any]:
    ents = splits()["VALIDATION"]["traces"]
    return {"artifact": "R4-01-VALIDATION-PLAN", "note": "Frozen before validation outcomes are read; contains no outcome.", "scorer": "phase1.py score (score_trace/assemble)", "split": "VALIDATION", "arms": ["A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"],
            "budget_bytes": G.BUDGET, "primary_endpoint": "capture(P) = sum(B_A - B_P) / sum(B_A - B_BELADY) over the 12 traces (decode miss bytes, exact rational)", "gate": "CHEAP capture >= 1/4 (VALIDATION half of rule A); reported, not acted on",
            "sensitivity_nongating": "same endpoint excluding r2-11-coding-v1 (prompt shared with the Research 3 stream)", "integrity": ["A reproduces the frozen R2-11 reference on every trace", "no budget violation", "pooled denominator > 0"],
            "executions": "exactly once; the scorer refuses to run unless VALIDATION_OPENED.json is committed and refuses to overwrite VALIDATION_RESULTS.json", "no_classification": "the final R4 class needs EVAL (Phase 2)",
            "traces": [{"workload_id": e["workload_id"], "category": e["nominal_category"], "trace_sha256": e["trace_sha256"], "source_path": sanitize(e["trace_path_host"]), "frozen_A_reference": {k: e["frozen_A_reference"][k] for k in ("decode_misses", "decode_bytes_missed", "decode_steps")}} for e in ents]}


def model_hashes() -> Dict[str, Any]:
    ch = jread(HERE / "CHEAP_MODEL.json"); rm = M.RichModel.from_json(jread(HERE / "RICH_MODEL.json"))
    return {"artifact": "R4-01-MODEL-HASHES", "cheap_tree_hash": ch["tree"]["hash"], "cheap_model_file_lf_sha256": lf_sha(HERE / "CHEAP_MODEL.json"), "cheap_rule_md_lf_sha256": lf_sha(HERE / "CHEAP_RULE.md"),
            "rich_model_digest": rm.digest(), "rich_model_file_lf_sha256": lf_sha(HERE / "RICH_MODEL.json"), "decayed_count_config_sha256": sha_bytes(canon({"decay": G.DECAY, "sweep": False, "tie": ["older reference (larger events_since_last_ref)", "layer", "expert"]})),
            "feature_contract_lf_sha256": lf_sha(P0 / "FEATURE_CONTRACT.json"), "feature_schema_hash": _feature_schema_hash(), "replay_engine_lf_sha256": lf_sha(HERE / "engine.py"), "model_contract_lf_sha256": lf_sha(P0 / "MODEL_CONTRACT.json")}


def open_record() -> Dict[str, Any]:
    if git("status", "--short", "--", "experiments", "paper", "argine"): raise P1Error("worktree not clean: refusing to open VALIDATION")
    mh = model_hashes(); imp = impl_record(); ents = splits()["VALIDATION"]["traces"]
    if imp["files_lf_sha256"] != jread(HERE / "IMPLEMENTATION.json")["files_lf_sha256"]: raise P1Error("implementation changed after IMPLEMENTATION.json")
    vh = {}
    for e in ents:
        h = trace_hash("VALIDATION", e)
        if h != e["trace_sha256"]: raise P1Error(f"VALIDATION trace hash mismatch {e['workload_id']}")
        vh[e["workload_id"]] = h
    return {"artifact": "R4-01-VALIDATION-OPENED", "state": "VALIDATION_OPENED_NO_OUTCOME", "implementation_commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"), "phase0_commit": PHASE0_COMMIT, "phase0_contract_digest": PHASE0_DIGEST, "phase0_freeze_sha256": PHASE0_FREEZE,
            "model_hashes": mh, "implementation_files_lf_sha256": imp["files_lf_sha256"], "cheap_rule_hash": mh["cheap_tree_hash"], "rich_model_hash": mh["rich_model_digest"], "feature_contract_hash": mh["feature_contract_lf_sha256"], "replay_hash": mh["replay_engine_lf_sha256"],
            "validation_plan_lf_sha256": lf_sha(HERE / "VALIDATION_PLAN.json"), "replay_fidelity_lf_sha256": lf_sha(HERE / "REPLAY_FIDELITY.json"), "validation_trace_sha256": vh, "validation_trace_count": len(vh), "eval_state": EVAL_STATE,
            "validation_outcome_contained": False, "rule": "no source change affecting policy behaviour is permitted after this record; scoring runs once"}


def committed(path: str) -> bool:
    return bool(git("log", "--format=%H", "-n", "1", "--", path)) and not git("status", "--short", "--", path)


BUDGET_S = 140.0


def score_guard() -> None:
    if (HERE / "VALIDATION_RESULTS.json").exists(): raise P1Error("VALIDATION already scored: refusing to run twice")
    if not (HERE / "VALIDATION_OPENED.json").exists() or not committed("experiments/R4-01-cfdc-training-validation/VALIDATION_OPENED.json"): raise P1Error("VALIDATION_OPENED.json is not committed: refusing to score")


def run_score() -> None:
    score_guard()
    op = jread(HERE / "VALIDATION_OPENED.json"); mh = model_hashes()
    if op["model_hashes"] != mh or op["implementation_files_lf_sha256"] != impl_record()["files_lf_sha256"]: raise P1Error("models or implementation changed after the opening record")
    if git("status", "--short", "--", "experiments", "paper", "argine"): raise P1Error("worktree not clean")
    out = score_all(BUDGET_S)
    if out is None: print("VALIDATION scoring INCOMPLETE; rerun (scored traces are cached and never recomputed)"); raise SystemExit(3)
    jwrite("VALIDATION_RESULTS.json", out["results"]); jwrite("VALIDATION_DECISION.json", out["decision"])


def provenance() -> Dict[str, Any]:
    sp = splits()
    return {"artifact": "R4-01-PROVENANCE", "phase0_commit": PHASE0_COMMIT, "phase0_contract_digest": PHASE0_DIGEST, "phase0_freeze_sha256": PHASE0_FREEZE, "source_r3_head": SOURCE_R3_HEAD, "python": sys.version.split()[0], "numpy": np.__version__, "implementation_commit": jread(HERE / "VALIDATION_OPENED.json")["implementation_commit"],
            "sources": {"TRAIN": [{"id": e["instance_id"], "sha256": e["trace_sha256"], "path": sanitize(e["trace_path_host"])} for e in sp["TRAIN"]["traces"]], "VALIDATION": [{"id": e["workload_id"], "sha256": e["trace_sha256"], "path": sanitize(e["trace_path_host"])} for e in sp["VALIDATION"]["traces"]],
                        "EVAL": "NOT READ (identity-only in Phase 0 DATA_SPLITS)"}, "r2_11_folder_access": "read-only; no file in the R2-11 source was modified, renamed, normalized or copied into the repository",
            "frozen_inputs": {"canonical_stream_sha256": CANON_STREAM_SHA, "dev_results_lf_sha256": lf_sha(R3 / "r4-nue-candidate-gate" / "DEV_RESULTS.json"), "r2_belady_results_lf_sha256": lf_sha(R3 / "r2-belady-ceiling" / "RESULTS.json"), "nue_x_eval_results_lf_sha256": lf_sha(R3 / "r4-nue-candidate-gate" / "EVAL_RESULTS.json")},
            "eval_trace_content_read": False, "eval_eviction_metrics_calculated": False, "model_inference_runs": 0}


def freeze() -> Dict[str, Any]:
    jwrite("PROVENANCE.json", provenance())
    files = ARTIFACTS + POST
    fz = {"artifact": "R4-01-FREEZE", "state": STATE_FINAL, "eval_state": EVAL_STATE, "phase0_contract_digest": PHASE0_DIGEST, "files_sha256": {f: lf_sha(HERE / f) for f in files + IMPL_FILES + TESTS}, "implementation_commit": jread(HERE / "VALIDATION_OPENED.json")["implementation_commit"],
          "classification_inputs": {"cheap_validation_capture": jread(HERE / "VALIDATION_DECISION.json")["cheap_capture_float"], "cheap_meets_validation_gate": jread(HERE / "VALIDATION_DECISION.json")["cheap_meets_validation_gate"]}, "next": "Phase 2 (EVAL opened once) only on explicit instruction"}
    fz["contract_digest"] = sha_bytes(canon({k: v for k, v in fz.items() if k not in ("freeze_sha256",)})); fz["freeze_sha256"] = freeze_sha(fz); jwrite("FREEZE.json", fz); return fz


def verify() -> List[str]:
    p: List[str] = []
    g = subprocess.run([sys.executable, str(P0 / "phase0.py"), "verify"], capture_output=True, text=True)
    if g.returncode != 0: p.append("phase0 verify failed")
    fz0 = jread(P0 / "FREEZE.json")
    if fz0["contract_digest"] != PHASE0_DIGEST or fz0["freeze_sha256"] != PHASE0_FREEZE: p.append("phase 0 identity differs")
    if (HERE / "FREEZE.json").exists():
        fz = jread(HERE / "FREEZE.json")
        if fz["freeze_sha256"] != freeze_sha(fz): p.append("phase 1 freeze sha mismatch")
        for f, h in fz["files_sha256"].items():
            if lf_sha(HERE / f) != h: p.append(f"{f} differs from the freeze")
        if fz["state"] != STATE_FINAL or fz["eval_state"] != "LOCKED": p.append("state/eval state")
    imp = jread(HERE / "IMPLEMENTATION.json")
    for f, h in imp["files_lf_sha256"].items():
        if lf_sha(HERE / f) != h: p.append(f"implementation file {f} changed after IMPLEMENTATION.json")
    if (HERE / "VALIDATION_OPENED.json").exists():
        op = jread(HERE / "VALIDATION_OPENED.json")
        if op["model_hashes"] != model_hashes(): p.append("model hashes differ from the opening record")
        if op["eval_state"] != "LOCKED" or op["validation_outcome_contained"]: p.append("opening record invalid")
    for f in sorted(HERE.rglob("*")):
        if f.is_file() and f.suffix in (".json", ".md") and any(s in f.read_text(encoding="utf-8", errors="ignore") for s in ("alpel", "/sessions/")): p.append(f"personal path in {f.name}")
    for rev, paths in ((SOURCE_R3_HEAD, ["experiments/R3-*", "paper/research3"]), (PHASE0_COMMIT, ["experiments/R4-00-causal-eviction-distillation"])):
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", rev, "--", *paths], capture_output=True, text=True)
        if r.stdout.strip(): p.append(f"files differ from {rev[:7]}: {r.stdout.split()[:4]}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["gate", "fidelity", "labels", "fit-cheap", "fit-rich", "pack", "open", "score", "freeze", "verify"]); ap.add_argument("--budget", type=float, default=140.0); a = ap.parse_args(); globals()["BUDGET_S"] = a.budget
    if a.cmd == "gate": o = gate(); print(json.dumps(o, indent=1)); return 0 if o["ok"] else 1
    if a.cmd == "fidelity":
        o = fidelity(budget_s=a.budget)
        if o.get("incomplete"): print("INCOMPLETE", o); return 3
        jwrite("REPLAY_FIDELITY.json", o); print("fidelity all_exact:", o["all_exact"], o["n_checks"]); return 0 if o["all_exact"] else 1
    if a.cmd == "labels": o = labels(); (WORK / "labels_manifest.json").write_text(dumps(o)); print(json.dumps(o["per_trace"], indent=1)); return 0
    if a.cmd == "fit-cheap": return fit_cheap_main()
    if a.cmd == "fit-rich": return fit_rich_main()
    if a.cmd == "pack": return pack_main()
    if a.cmd == "open": jwrite("VALIDATION_OPENED.json", open_record()); print("opened (hash-only record written)"); return 0
    if a.cmd == "score": run_score(); print("VALIDATION scored"); return 0
    if a.cmd == "freeze": fz = freeze(); print("FREEZE", fz["freeze_sha256"]); return 0
    p = verify(); print("VERIFY OK" if not p else "VERIFY FAILED: " + "; ".join(p)); return 0 if not p else 1


# the three heavier commands live below to keep the scorer above compact
def fit_cheap_main() -> int:
    X, y, g = load_train(); t = time.time(); tf = WORK / "cheap_tree.json"
    if tf.exists(): tree = jread(tf)
    else:
        tree = M.fit_cheap(X, y, g); tf.write_text(dumps({**tree, "_fit_seconds": round(time.time() - t, 1)})); print("tree fitted in", round(time.time() - t, 1), "s; rerun for diagnostics"); return 3
    fit_s = tree.pop("_fit_seconds", None)
    names = tree["features_used"]; leaves = [i for i, f in enumerate(tree["feature"]) if f < 0]
    audit = {"n_train_rows": int(len(y)), "n_train_decisions": int(len(np.unique(g))), "depth": max(nd["depth"] for nd in tree["node_stats"]), "leaf_count": len(leaves), "distinct_features": len(names),
             "min_leaf_rows": min(tree["node_stats"][i]["n_rows"] for i in leaves), "min_leaf_decisions": min(tree["node_stats"][i]["n_decisions"] for i in leaves),
             "integer_thresholds": all(isinstance(v, int) for v in tree["threshold"]), "integer_leaf_ranks": all(isinstance(v, int) for v in tree["leaf_rank"]), "fit_seconds": fit_s}
    # TRAIN-only non-gating shift diagnostic: CHEAP's victim vs the teacher maximum under A's states and under CHEAP's own states
    pol = G.PolCheap(tree); diag = {"agree_under_A_states": [], "agree_under_CHEAP_states": []}
    for e in splits()["TRAIN"]["traces"]:
        es = G.from_trace(load_trace("TRAIN", e), G.BUDGET, ident("TRAIN", e)); o = G.teacher_rows(es, probe=pol); s = o["stats"]
        ag = G.agreement_run(es, G.PolCheap(tree))
        diag["agree_under_A_states"].append({"trace": e["instance_id"], "sampled": s["sampled_decisions"], "agree": s["probe_agree_under_A_states"]})
        diag["agree_under_CHEAP_states"].append({"trace": e["instance_id"], "sampled": ag["sampled"], "agree": ag["agree"], "decode_miss_bytes_on_TRAIN": ag["replay"]["decode"]["miss_bytes"]})
    out = {"artifact": "R4-01-CHEAP-MODEL", "role": "the single frozen CHEAP candidate (fit once on TRAIN; one predefined four-feature refit if needed)", "tree": tree, "train_audit": audit, "train_shift_diagnostic_nongating": diag,
           "deployment": "integer comparisons on integer features; leaf ranks are integers; evict the highest leaf rank, ties: larger events_since_last_ref, layer, expert"}
    jwrite("CHEAP_MODEL.json", out)
    (HERE / "CHEAP_RULE.md").write_text("# CHEAP rule (frozen on TRAIN)\n\nEvict, among the candidates (residents not requested by the current event; all residents if none), the one with the HIGHEST leaf rank.\nTies: larger `events_since_last_ref` (older), then lower layer, then lower expert.\n\n```\n" + M.cheap_rule_text(tree) + "\n```\n\n"
                                         f"Features used: {', '.join(names)}.  Tree hash: `{tree['hash']}`.\n", encoding="utf-8", newline="\n")
    print(json.dumps(audit, indent=1)); print(M.cheap_rule_text(tree)); return 0


def fit_rich_main() -> int:
    X, y, g = load_train(); logf = open(WORK / "rich_fit.log", "a"); log = lambda s: (logf.write(s + "\n"), logf.flush())
    t = time.time(); m = M.fit_rich(X, y, log=log, ckpt=str(WORK / "rich_ckpt.npz"), time_budget=float(os.environ.get("R4_BUDGET", "120")))
    if m is None: print("RICH fit INCOMPLETE; rerun"); return 3
    sec = round(time.time() - t, 1)
    mj = m.to_json(); mj.update({"artifact": "R4-01-RICH-MODEL", "role": "diagnostic information ceiling of the frozen feature vocabulary; not a runtime candidate", "train_rows": int(len(y)), "digest": m.digest()})
    pr = m.predict_float_numpy(X[:100000]); mj["train_rmse_first_100k_rows"] = float(np.sqrt(np.mean((y[:100000] - pr) ** 2))); mj["c_vs_numpy_inference_identical_on_20k_rows"] = bool((m.predict_q(X[:20000]) == m.predict_q_numpy(X[:20000])).all())
    jwrite("RICH_MODEL.json", mj); log("done " + m.digest()); print("RICH digest", m.digest(), "seconds", sec); return 0


def train_manifests() -> Dict[str, Any]:
    lm = jread(WORK / "labels_manifest.json"); X, y, g = load_train()
    q = lambda a: [float(v) for v in np.quantile(a, [0, 0.01, 0.25, 0.5, 0.75, 0.99, 1.0])]
    feats = {nm: {"min": int(X[:, i].min()), "max": int(X[:, i].max()), "mean": float(X[:, i].mean()), "quantiles_0_1_25_50_75_99_100": q(X[:, i].astype(np.float64))} for i, nm in enumerate(G.FEATURE_ORDER)}
    tgt = {"rows": int(len(y)), "mean": float(y.mean()), "std": float(y.std()), "never_used_again_fraction": float((y == G.Y_INF).mean()), "quantiles_0_1_25_50_75_99_100": q(y)}
    tm = {"artifact": "R4-01-TEACHER-MANIFEST", "teacher": "farthest-future next-use distance d (served events) of every shielded candidate at A's every-64th eviction decision; y = log2(1+d), 21.0 for never used again", "per_trace": lm["per_trace"], "feature_schema_hash": lm["feature_schema_hash"],
          "total_sampled_decisions": sum(t["sampled_decisions"] for t in lm["per_trace"]), "total_candidate_rows": sum(t["candidate_rows"] for t in lm["per_trace"]), "all_labels_sha256": sha_bytes(y.astype("<f8").tobytes()), "all_features_sha256": sha_bytes(X.astype("<i4").tobytes()),
          "future_information_in_features": False, "target_distribution": tgt}
    tr = {"artifact": "R4-01-TRAIN-MANIFEST", "split": "TRAIN (4 DEV seed-42 traces)", "traces": [{"trace": t["trace"], "source_trace_sha256": t["source_trace_sha256"], "served_events": t["served_events"], "a_eviction_decisions": t["a_eviction_decisions"], "sampled_decisions": t["sampled_decisions"], "candidate_rows": t["candidate_rows"]} for t in lm["per_trace"]],
          "total_a_eviction_decisions": sum(t["a_eviction_decisions"] for t in lm["per_trace"]), "total_sampled_decisions": tm["total_sampled_decisions"], "total_candidate_rows": tm["total_candidate_rows"], "feature_distributions": feats, "target_distribution": tgt, "seed43_used": False,
          "raw_rows": "not committed (regenerable by `phase1.py labels`; hashes above)"}
    return {"teacher": tm, "train": tr}


def pack_main() -> int:
    tm = train_manifests(); jwrite("TEACHER_MANIFEST.json", tm["teacher"]); jwrite("TRAIN_MANIFEST.json", tm["train"])
    fc = jread(P0 / "FEATURE_CONTRACT.json")
    jwrite("FEATURE_IMPLEMENTATION.json", {"artifact": "R4-01-FEATURE-IMPLEMENTATION", "feature_count": len(G.FEATURE_ORDER), "order": G.FEATURE_ORDER, "contract_lf_sha256": lf_sha(P0 / "FEATURE_CONTRACT.json"), "implementation": "engine.py Dec.col / Sim state arrays (see IMPLEMENTATION.json interpretations)",
                                          "features": [{"id": f["id"], "name": f["name"], "contract_definition": f["definition"], "units": f["units"], "depends_on_future": f["depends_on_future"]} for f in fc["features"]], "future_features": 0, "causality_tests": "tests/test_phase1.py::test_future_routing_cannot_change_features",
                                          "interpretations": INTERPRETATIONS})
    jwrite("IMPLEMENTATION.json", impl_record()); jwrite("MODEL_HASHES.json", model_hashes()); jwrite("VALIDATION_PLAN.json", pack_plan()); print("packed"); return 0


if __name__ == "__main__":
    sys.exit(main())
