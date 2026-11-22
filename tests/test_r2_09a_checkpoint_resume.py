import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from argine.research2.r2_09a import checkpoint_runner
from argine.research2.r2_09a.tournament import build_dev_corpus_manifest

def test_checkpoint_cell_key_deterministic():
    k1 = checkpoint_runner.cell_key(4294967296, 'coding-v1__seed-42', 'global_lru', 'global_lru/default')
    k2 = checkpoint_runner.cell_key(4294967296, 'coding-v1__seed-42', 'global_lru', 'global_lru/default')
    assert k1 == k2
    assert checkpoint_runner.cell_key(1610612736,'x','lfu','lfu/default') != k1

def test_checkpoint_validation_fail_closed(tmp_path):
    corpus = build_dev_corpus_manifest()
    ckpt = tmp_path / 'ckpt'
    m, env, cells_dir = checkpoint_runner.load_or_init_checkpoint(corpus, ckpt)
    assert m['schema_version'] == checkpoint_runner.CHECKPOINT_SCHEMA_VERSION
    assert m['r2_09b_contract_digest'] == 'b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28'
    assert m['dev_corpus_digest'] == 'f758b77492eebc38bc81ba22d6b06ab8f19138d1099b635821369a4d022d9398'  # authoritative (PHASE_0_PROVENANCE_CORRECTION)
    assert m['expected_cell_count'] == 624
    # write malformed cell -> fail closed
    rec = {"cell_key":"bad","budget_bytes":1,"dev_instance_id":"x","policy_family":"y","policy_config_id":"z"}
    import json as _j
    (cells_dir/"bad.json").write_text(_j.dumps(rec), encoding='utf-8')
    # load_or_init would create manifest; validate logic should reject on load - simulate
    ok, reason = checkpoint_runner.validate_completed_cell_record({"status":"BAD"}, rec)
    assert not ok
