from __future__ import annotations
import csv, hashlib, json, os, subprocess, sys, time, uuid
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from argine.research.trace import Trace
from argine.research2.r2_09b.contract import compute_contract_digest, assert_valid
from .policies import build_tournament_configs, PRIMARY_BUDGETS, PRIMARY_BUDGET_LABELS
from .tournament import (
    REPO_ROOT, EVIDENCE_ROOT, OUT_DIR, R2_03_REUSE, CANONICAL_R2_01_TRACE_SHA,
    _canon_json, _digest, trace_decode_steps, build_dev_corpus_manifest, _replay_once
)

CHECKPOINT_SCHEMA_VERSION = 1
RUNNER_VERSION = "r2-09a-checkpoint-runner-v1"

def cell_key(budget:int, instance_id:str, family:str, config_id:str)->str:
    # unique canonical identity
    raw = f"{budget}|{instance_id}|{family}|{config_id}"
    h = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
    safe = f"{budget}__{instance_id}__{family}__{config_id.replace('/','_').replace('=','-').replace(',','_').replace('.','p')}"
    return safe[:180] + "__" + h

def atomic_write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    data = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    # Windows-compatible robust replace: bounded retry for transient PermissionError/sharing violations
    last_err = None
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    # fail visibly; keep tmp for diagnosis but do not overwrite target non-atomically
    raise last_err

def load_json(path: Path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _git(cmd):
    r=subprocess.run(cmd,cwd=REPO_ROOT,capture_output=True,text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def expected_configs():
    return build_tournament_configs()

def configs_digest(cfgs):
    return _digest([{ "family_id":c["family_id"], "config_id":c["config_id"], "params":c.get("params",{}) } for c in cfgs])

def validate_checkpoint_env(corpus, ckpt_dir: Path):
    v = assert_valid()
    contract_digest = v["digest"]
    expected_contract = "b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28"
    if contract_digest != expected_contract:
        raise RuntimeError(f"contract digest mismatch {contract_digest}")
    corpus_digest = corpus.get("dev_corpus_digest")
    expected_corpus = "f758b77492eebc38bc81ba22d6b06ab8f19138d1099b635821369a4d022d9398"
    if corpus_digest != expected_corpus:
        raise RuntimeError(f"corpus digest mismatch {corpus_digest} expected {expected_corpus}")
    cfgs = expected_configs()
    return {"contract_digest": contract_digest, "corpus_digest": corpus_digest, "configs_digest": configs_digest(cfgs), "configs": cfgs}

def load_or_init_checkpoint(corpus, ckpt_dir: Path):
    env = validate_checkpoint_env(corpus, ckpt_dir)
    manifest_path = ckpt_dir / "manifest.json"
    cells_dir = ckpt_dir / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    expected_count = len(PRIMARY_BUDGETS) * len(corpus["selected_instances"]) * len(env["configs"])
    if manifest_path.exists():
        m = load_json(manifest_path)
        # fail closed on mismatch
        if m.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise RuntimeError("checkpoint schema mismatch")
        if m.get("r2_09b_contract_digest") != env["contract_digest"]:
            raise RuntimeError("checkpoint contract digest mismatch")
        if m.get("dev_corpus_digest") != env["corpus_digest"]:
            raise RuntimeError("checkpoint corpus digest mismatch")
        if m.get("baseline_configs_digest") != env["configs_digest"]:
            raise RuntimeError("checkpoint configs digest mismatch")
        if m.get("expected_cell_count") != expected_count:
            raise RuntimeError("checkpoint expected cell count mismatch")
        # keep generation id as-is
    else:
        m = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "runner_version": RUNNER_VERSION,
            "execution_generation_id": uuid.uuid4().hex,
            "creation_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_update_utc": None,
            "r2_09b_contract_digest": env["contract_digest"],
            "dev_corpus_digest": env["corpus_digest"],
            "baseline_configs_digest": env["configs_digest"],
            "expected_cell_count": expected_count,
            "completed_cell_count": 0,
            "failed_cell_count": 0,
            "pending_cell_count": expected_count,
            "execution_history": [],
        }
        atomic_write_json(manifest_path, m)
    return m, env, cells_dir

def validate_completed_cell_record(rec, expected):
    # identity
    for k in ["budget_bytes","dev_instance_id","policy_family","policy_config_id","cell_key"]:
        if rec.get(k) != expected.get(k):
            return False, f"identity mismatch {k}"
    # required fields
    req = ["misses","miss_rate","miss_bytes","miss_bytes_per_decode_step","misses_per_decode_step","evictions","peak_resident_bytes","budget_violations","evidence_mode","result_digest","deterministic","status","decode_steps","accesses"]
    for k in req:
        if k not in rec:
            return False, f"missing {k}"
    if rec.get("status") != "OK" or not rec.get("deterministic"):
        return False, "cell not complete/valid"
    if int(rec.get("budget_violations",1)) != 0:
        return False, "budget violations"
    if int(rec.get("peak_resident_bytes",10**30)) > int(rec.get("budget_bytes",0)):
        return False, "peak resident exceeds budget"
    # digest self-check (canonical subset)
    check_obj = {k: rec[k] for k in ["budget_bytes","dev_instance_id","nominal_category","policy_family","policy_config_id","misses","miss_rate","miss_bytes","miss_bytes_per_decode_step","misses_per_decode_step","evictions","peak_resident_bytes","budget_violations","evidence_mode","source_run_id","result_digest","deterministic","status","decode_steps","accesses","causal"]}
    if rec.get("cell_record_digest") != _digest(check_obj):
        return False, "cell_record_digest mismatch"
    return True, "ok"

def run_checkpointed(corpus, ckpt_dir: Path, progress_every=10):
    manifest, env, cells_dir = load_or_init_checkpoint(corpus, ckpt_dir)
    cfgs = env["configs"]
    traces = {}
    for inst in corpus["selected_instances"]:
        traces[inst["instance_id"]] = Trace.load(inst["trace_path"])
    # index existing valid completed cells
    completed = {}
    failed = {}
    expected_cells = []
    for budget in PRIMARY_BUDGETS:
        for inst in corpus["selected_instances"]:
            for cfg in cfgs:
                ck = cell_key(budget, inst["instance_id"], cfg["family_id"], cfg["config_id"])
                expected_cells.append((budget, inst, cfg, ck))
    for _, inst, cfg, ck in expected_cells:
        p = cells_dir / f"{ck}.json"
        if not p.exists():
            continue
        try:
            rec = load_json(p)
        except Exception as e:
            raise RuntimeError(f"fail closed: unreadable checkpoint cell {p}: {e}")
        ok, reason = validate_completed_cell_record(rec, {
            "budget_bytes": rec.get("budget_bytes"), "dev_instance_id": inst["instance_id"],
            "policy_family": cfg["family_id"], "policy_config_id": cfg["config_id"], "cell_key": ck
        })
        if ok:
            completed[ck] = rec
        else:
            # malformed/invalid checkpoint -> fail closed (do not trust/repair)
            raise RuntimeError(f"fail closed: invalid checkpoint cell {p}: {reason}")
    pending = [c for c in expected_cells if c[3] not in completed]
    manifest["completed_cell_count"] = len(completed)
    manifest["failed_cell_count"] = 0
    manifest["pending_cell_count"] = len(pending)
    manifest["last_update_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest.setdefault("execution_history", []).append({
        "event": "resume_or_start", "pid": os.getpid(), "utc": manifest["last_update_utc"],
        "completed": len(completed), "pending": len(pending)
    })
    atomic_write_json(ckpt_dir / "manifest.json", manifest)
    total = len(expected_cells)
    done_initial = len(completed)
    done = done_initial
    print(f"checkpoint loaded complete={done_initial} pending={len(pending)} total={total}", flush=True)

    for budget, inst, cfg, ck in pending:
        trace = traces[inst["instance_id"]]
        decode_steps = inst["decode_steps"]
        family = cfg["family_id"]
        config_id = cfg["config_id"]
        reuse = None
        if inst["trace_sha256"] == CANONICAL_R2_01_TRACE_SHA and family in ("global_lru", "lfu"):
            key_name = "Global-LRU" if family == "global_lru" else "LFU"
            reuse = R2_03_REUSE.get((budget, key_name))
        if reuse:
            r1 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
            r2 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
            if r1["deterministic_replay_digest"] != r2["deterministic_replay_digest"]:
                raise RuntimeError(f"FAILED_NONDETERMINISTIC {ck}")
            if r1["miss_bytes"] != reuse["miss_bytes"] or r1["misses"] != reuse["misses"]:
                evidence_mode = "NEW_R2_09A_REPLAY_REUSE_MISMATCH"
                source_run_id = "R2-09A"
            else:
                evidence_mode = "REUSED_CANONICAL_DEV_EVIDENCE"
                source_run_id = reuse["source_run_id"]
            r = r1
        else:
            r1 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
            r2 = _replay_once(trace, inst["trace_sha256"], cfg, budget)
            if r1["deterministic_replay_digest"] != r2["deterministic_replay_digest"]:
                raise RuntimeError(f"FAILED_NONDETERMINISTIC {ck}")
            r = r1
            evidence_mode = "NEW_R2_09A_REPLAY"
            source_run_id = "R2-09A"
        if r.get("budget_violations", 0) != 0 or r.get("peak_resident_bytes", 0) > budget:
            raise RuntimeError(f"FAILED_BUDGET_INVARIANT {ck}")
        rec = {
            "cell_key": ck,
            "budget_bytes": budget,
            "budget_gib": {1610612736: 1.5, 4294967296: 4.0, 8589934592: 8.0}[budget],
            "dev_instance_id": inst["instance_id"],
            "nominal_category": inst["nominal_workload_category"],
            "policy_family": family,
            "policy_config_id": config_id,
            "causal": True,
            "misses": int(r["misses"]),
            "miss_rate": (float(r["misses"]) / float(r["accesses"])) if r.get("accesses") else None,
            "miss_bytes": int(r["miss_bytes"]),
            "miss_bytes_per_decode_step": (float(r["miss_bytes"]) / float(decode_steps)) if decode_steps else None,
            "misses_per_decode_step": (float(r["misses"]) / float(decode_steps)) if decode_steps else None,
            "evictions": int(r.get("evictions", 0)),
            "peak_resident_bytes": int(r.get("peak_resident_bytes", 0)),
            "budget_violations": int(r.get("budget_violations", 0)),
            "evidence_mode": evidence_mode,
            "source_run_id": source_run_id,
            "result_digest": r["deterministic_replay_digest"],
            "deterministic": True,
            "status": "OK",
            "decode_steps": decode_steps,
            "accesses": int(r.get("accesses", 0)),
            "params": cfg.get("params", {}),
        }
        check_obj = {k: rec[k] for k in ["budget_bytes","dev_instance_id","nominal_category","policy_family","policy_config_id","misses","miss_rate","miss_bytes","miss_bytes_per_decode_step","misses_per_decode_step","evictions","peak_resident_bytes","budget_violations","evidence_mode","source_run_id","result_digest","deterministic","status","decode_steps","accesses","causal"]}
        rec["cell_record_digest"] = _digest(check_obj)
        # atomic durable write only after all validation passes
        atomic_write_json(cells_dir / f"{ck}.json", rec)
        completed[ck] = rec
        done += 1
        if done % progress_every == 0 or done == total:
            manifest["completed_cell_count"] = len(completed)
            manifest["pending_cell_count"] = total - len(completed)
            manifest["last_update_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            atomic_write_json(ckpt_dir / "manifest.json", manifest)
            print(f"progress {done}/{total}", flush=True)
    # final manifest
    manifest["completed_cell_count"] = len(completed)
    manifest["failed_cell_count"] = 0
    manifest["pending_cell_count"] = 0
    manifest["last_update_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest.setdefault("execution_history", []).append({"event":"complete","utc":manifest["last_update_utc"],"completed":len(completed)})
    atomic_write_json(ckpt_dir / "manifest.json", manifest)
    # return rows in deterministic order
    rows = []
    for budget, inst, cfg, ck in expected_cells:
        rows.append(completed[ck])
    return rows, manifest, env
