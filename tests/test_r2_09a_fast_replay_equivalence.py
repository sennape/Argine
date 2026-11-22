import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from argine.research.trace import Trace
from argine.research2.r2_09a.policies import build_tournament_configs
from argine.research2.r2_09a.tournament import _replay_once, CANONICAL_R2_01_TRACE, CANONICAL_R2_01_TRACE_SHA
from argine.research2.r2_09a.fast_replay import run_fast

R2_08_BASE = r"C:\Users\alpel\AppData\Local\Argine3\research\R2-08\captures\6165471a12574e05b48932aac2f1626c"
TRACES = [
    (CANONICAL_R2_01_TRACE, CANONICAL_R2_01_TRACE_SHA),
    (R2_08_BASE + r"\reasoning-v1-seed42\run-00-4G\trace.jsonl.gz", "8dff85b9e99f6e10a2a6a608585c77102c65597561c2cf55dd4471e37eb48b3b"),
    (R2_08_BASE + r"\instruction-v1-seed42\run-00-4G\trace.jsonl.gz", "9934371d18a1ad3f0e2cb2a5e89cad87ed4e7a002732190646cfff9a0b489f54"),
]
BUDGETS = [1610612736, 4294967296, 8589934592]

def test_fast_replay_rejected_canonical_digest_mismatch():
    """fast_replay omits per_step arrays so canonical deterministic_replay_digest differs; cannot be used canonically."""
    tr = Trace.load(TRACES[0][0])
    cfg = [c for c in build_tournament_configs() if c['config_id']=='global_lru/default'][0]
    ref = _replay_once(tr, TRACES[0][1], cfg, BUDGETS[0])
    det, _ = run_fast(tr, cfg['factory'](), BUDGETS[0], phase='decode', trace_digest=TRACES[0][1])
    # core metrics equal
    assert det['misses'] == ref['misses']
    assert det['bytes_missed'] == ref['miss_bytes']
    assert det['evictions'] == ref['evictions']
    assert det['peak_resident'] == ref['peak_resident_bytes']
    # but canonical digest cannot match because per_step arrays are None
    assert det['per_step_misses'] is None
    # therefore FAST_PATH_REJECTED for canonical tournament evidence (digest compatibility required for R2-03 reuse/contract)
