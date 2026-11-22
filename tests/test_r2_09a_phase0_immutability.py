import json, hashlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from argine.research2.r2_09a.tournament import build_dev_corpus_manifest, load_frozen_dev_corpus_manifest, OUT_DIR, AUTHORITATIVE_DEV_CORPUS_DIGEST

def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(',',':'), ensure_ascii=False)

def digest(obj):
    return hashlib.sha256(canon(obj).encode('utf-8')).hexdigest()

def test_phase0_manifest_immutable_after_freeze():
    p = OUT_DIR / 'dev_corpus_manifest.json'
    assert p.exists(), 'frozen manifest must exist'
    before_bytes = p.read_bytes()
    before = json.loads(before_bytes.decode('utf-8'))
    assert before.get('dev_corpus_digest') == AUTHORITATIVE_DEV_CORPUS_DIGEST
    # repeated load/resume path must not alter file or digest
    m1 = build_dev_corpus_manifest()
    m2 = load_frozen_dev_corpus_manifest(p)
    after_bytes = p.read_bytes()
    after = json.loads(after_bytes.decode('utf-8'))
    assert before_bytes == after_bytes
    assert m1['dev_corpus_digest'] == AUTHORITATIVE_DEV_CORPUS_DIGEST
    assert m2['dev_corpus_digest'] == AUTHORITATIVE_DEV_CORPUS_DIGEST
    # stored digest self-consistent
    recomputed = digest({k:v for k,v in after.items() if k != 'dev_corpus_digest'})
    assert recomputed == AUTHORITATIVE_DEV_CORPUS_DIGEST
    # scientific content stable (selected instances / decode_steps)
    assert after['counts']['selected'] == 8
    ds = {i['instance_id']: i['decode_steps'] for i in after['selected_instances']}
    assert ds == {
        'coding-v1__seed-42': 381, 'coding-v1__seed-43': 381,
        'instruction-v1__seed-42': 511, 'instruction-v1__seed-43': 511,
        'reasoning-v1__seed-42': 511, 'reasoning-v1__seed-43': 511,
        'summarization-v1__seed-42': 511, 'summarization-v1__seed-43': 511,
    }
