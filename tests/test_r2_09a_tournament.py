"""R2-09A DEV tournament tests (no model, no EVAL inspection)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research.trace import Trace
from argine.research2.r2_09a.policies import GlobalLRU, LFU, TwoQ, LRUK, AgedFrequency, build_tournament_configs, PRIMARY_BUDGETS
from argine.research2.r2_09a.tournament import build_dev_corpus_manifest, trace_decode_steps, _replay_once
from argine.research2.r2_09b.contract import assert_valid

R2_01_TRACE = r"C:\Users\alpel\AppData\Local\Argine3\research\R2-01\captures\286a41477ce54d5fb9ccc24f4e508f22\run-00-4G\trace.jsonl.gz"
R2_01_SHA = "e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1"


def load_r2_01():
    return Trace.load(R2_01_TRACE)


def test_contract_v1_1_valid_and_eval_locked():
    v = assert_valid()
    assert v["digest"] == "b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28"
    em = json.loads((Path(__file__).resolve().parents[1] / "experiments/R2-09-prediction-benchmark/eval_manifest.json").read_text(encoding="utf-8"))
    assert em["eval_visibility_state"] == "EVAL_LOCKED"
    assert em.get("eval_results") in (None, [], {})
    assert em.get("opened_utc") is None


def test_baseline_config_space_complete():
    cfgs = build_tournament_configs()
    fams = {}
    for c in cfgs:
        fams.setdefault(c["family_id"], []).append(c["config_id"])
    assert fams["global_lru"] == ["global_lru/default"]
    assert fams["lfu"] == ["lfu/default"]
    assert len(fams["twoq"]) == 9
    assert len(fams["lru_k"]) == 6
    assert len(fams["aged_frequency"]) == 9
    assert set(fams.keys()) == {"global_lru","lfu","twoq","lru_k","aged_frequency"}
    # all causal
    tr = load_r2_01()
    for c in cfgs:
        p = c["factory"]()
        assert getattr(p, "causal", True) is True or "causal" in str(p.describe()).lower()


def test_dev_corpus_frozen_and_valid():
    m = build_dev_corpus_manifest()
    assert m["counts"]["selected"] == 8
    assert m["counts"]["excluded"] == 0
    ids = [i["cell_id"] for i in m["selected_instances"]]
    assert "coding-v1/seed-42" in ids
    for i in m["selected_instances"]:
        assert i["decode_steps"] > 0
        assert i["top_k"] == 8
        assert i["layer_count"] == 48
        assert i["n_experts"] == 128
        assert i["expert_bytes_present"] is True
    assert m["dev_corpus_digest"] == hashlib.sha256(json.dumps({k:v for k,v in m.items() if k!="dev_corpus_digest"}, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def test_global_lru_lfu_match_r2_03_canonical_reuse_cases():
    tr = load_r2_01()
    # R2-03 canonical values
    cases = [
        (1610612736, "global_lru", 234799792128, 81752),
        (4294967296, "global_lru", 102151507968, 35529),
        (8589934592, "global_lru", 22898589696, 7952),
        (1610612736, "lfu", 245057458176, 85428),
        (4294967296, "lfu", 112122408960, 39124),
        (8589934592, "lfu", 25641676800, 8956),
    ]
    cfgs = {c["config_id"]: c for c in build_tournament_configs()}
    for b,fam,mb,misses in cases:
        cid = "global_lru/default" if fam=="global_lru" else "lfu/default"
        r1 = _replay_once(tr, R2_01_SHA, cfgs[cid], b)
        r2 = _replay_once(tr, R2_01_SHA, cfgs[cid], b)
        assert r1["deterministic_replay_digest"] == r2["deterministic_replay_digest"]
        assert r1["budget_violations"] == 0
        assert r1["miss_bytes"] == mb, (b,fam,r1["miss_bytes"],mb)
        assert r1["misses"] == misses
        assert r1["counted_steps"] == trace_decode_steps(tr) == 381


def test_new_baselines_deterministic_byte_capacity_r2_01():
    tr = load_r2_01()
    cfgs = [c for c in build_tournament_configs() if c["family_id"] in ("twoq","lru_k","aged_frequency")]
    # sample a few configs and budgets for speed
    sample = cfgs[0:3] + cfgs[9:12] + cfgs[15:18]
    for b in [1610612736, 4294967296, 8589934592]:
        for c in sample:
            r1 = _replay_once(tr, R2_01_SHA, c, b)
            r2 = _replay_once(tr, R2_01_SHA, c, b)
            assert r1["deterministic_replay_digest"] == r2["deterministic_replay_digest"], c["config_id"]
            assert r1["budget_violations"] == 0
            assert r1["peak_resident_bytes"] <= b
            assert r1["counted_steps"] == trace_decode_steps(tr) == 381
            assert r1["miss_bytes"] >= 0 and r1["misses"] >= 0
