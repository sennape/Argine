"""R2-09B contract validation tests (preregistration/freeze only).

No model execution, no EVAL capture, no R2-09A experiments.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argine.research2.r2_09b.contract import (
    CONTRACT_VERSION,
    DEV_WORKLOAD_SPECS,
    PRIMARY_BUDGETS_GIB,
    compute_contract_digest,
    default_contract_dir,
    load_contract,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = default_contract_dir()


def _read(name: str):
    return json.loads((CONTRACT_DIR / name).read_text(encoding='utf-8'))


def test_contract_loads_and_validates():
    errors = validate_contract(CONTRACT_DIR)
    assert errors == [], errors


def test_contract_digest_deterministic_and_recorded():
    d1 = compute_contract_digest(CONTRACT_DIR)
    d2 = compute_contract_digest(CONTRACT_DIR)
    assert d1 == d2
    c = _read('contract.json')
    assert c['contract_digest'] == d1
    freeze = json.loads((ROOT / 'paper/research2/R2-09B-FREEZE.json').read_text(encoding='utf-8'))
    assert freeze['contract_digest'] == d1


def test_no_eval_results_and_locked():
    em = _read('eval_manifest.json')
    assert em['eval_visibility_state'] == 'EVAL_LOCKED'
    assert em.get('eval_results') in (None, [], {})
    assert em.get('opened_utc') is None


def test_eval_specs_no_dev_overlap_and_hashes_match():
    em = _read('eval_manifest.json')
    dev_hashes = {s['sha256'] for s in DEV_WORKLOAD_SPECS}
    dev_paths = {s['source_path'] for s in DEV_WORKLOAD_SPECS}
    dev_ids = {s['workload_id'] for s in DEV_WORKLOAD_SPECS}
    ids = []
    hashes = []
    for s in em['eval_workload_specs']:
        assert s['split'] == 'eval'
        assert s['workload_id'] not in dev_ids
        assert s['source_path'] not in dev_paths
        assert s['sha256'] not in dev_hashes
        ids.append(s['workload_id'])
        hashes.append(s['sha256'])
        p = ROOT / s['source_path']
        assert p.exists(), p
        assert hashlib.sha256(p.read_bytes()).hexdigest() == s['sha256']
        src = s.get('source_input')
        if src and src.get('path'):
            sp = ROOT / src['path']
            assert sp.exists()
            assert hashlib.sha256(sp.read_bytes()).hexdigest() == src['sha256']
    assert len(ids) == len(set(ids))
    assert len(hashes) == len(set(hashes))
    families = {s['semantic_category'] for s in em['eval_workload_specs']}
    assert families == {'coding', 'reasoning', 'summarization', 'instruction'}


def test_primary_hypotheses_unique_and_budget_workload_cells():
    dr = _read('decision_rules.json')
    hyps = dr['primary_hypotheses']
    ids = [h['hypothesis_id'] for h in hyps]
    assert len(ids) == len(set(ids))
    assert ids, 'no primary hypotheses'
    # 3 primary budgets x 4 workload families x deterministic = 12
    assert len(hyps) == 12
    cells = {(h['budget_gib'], h['workload_family'], h['decoding_condition']) for h in hyps}
    expected = {(b, w, 'deterministic') for b in PRIMARY_BUDGETS_GIB for w in ['coding','reasoning','summarization','instruction']}
    assert cells == expected
    for h in hyps:
        assert h['budget_gib'] in PRIMARY_BUDGETS_GIB
        assert h['evaluation_state'] == 'EVAL_LOCKED'
        assert h.get('eval_result') is None
        assert h['confirmatory'] is True
        assert h.get('metric_direction')


def test_budgets_and_16gib_not_primary():
    c = _read('contract.json')
    assert tuple(c['primary_budgets_gib']) == PRIMARY_BUDGETS_GIB
    assert 16.0 not in c['primary_budgets_gib'] and 16 not in c['primary_budgets_gib']
    assert '16.0' in json.dumps(c['excluded_primary_budgets']) or '16' in json.dumps(c['excluded_primary_budgets'])


def test_baseline_families_have_frozen_spaces_and_causal():
    bs = _read('baseline_space.json')
    fams = {f['family_id']: f for f in bs['baseline_families']}
    for req in ['global_lru','lfu','twoq','lru_k','aged_frequency']:
        assert req in fams
    for f in bs['baseline_families']:
        assert f['causal'] is True
        hp = f.get('hyperparameters', {})
        if hp.get('tunable'):
            assert hp.get('search_space'), f['family_id']


def test_case_c_and_recovery_semantics():
    c = _read('contract.json')
    cc = c['case_c']
    assert cc['o_b_proven_byte_optimal'] is False
    rob = cc['recovery_of_belady_gap']
    assert rob['signed'] is True
    assert rob['allows_negative'] is True
    assert rob['allows_greater_than_one'] is True


def test_exp025_and_threshold():
    c = _read('contract.json')
    assert c['exp025']['classification'] == 'P2_FULL_STREAM_DISTRIBUTION_MODEL'
    assert c['exp025']['arbitrary_policy_stream_transferability'] == 'UNSUPPORTED'
    assert c['exp025']['direct_bytes_to_seconds_forbidden'] is True
    th = c['thresholds']
    assert th['status'] == 'PENDING_POLICY_STREAM_CALIBRATION'
    assert th['numeric_threshold_frozen'] is False
    assert th['r2_09a_blocked'] is False


def test_lfu_referenced_not_rerun():
    c = _read('contract.json')
    ed = c['existing_dev_evidence']
    assert ed['r2_03_run_id'] == 'd957077073374f1e9fd1e101a002946f'
    assert ed['lfu_classification'] == 'LFU_SWEEP_CANONICAL_AUXILIARY'
    assert ed['rerun_lfu_sweep'] is False


def test_stochastic_unresolved_deterministic_defined():
    c = _read('contract.json')
    assert c['stochastic_condition']['status'] == 'UNRESOLVED'
    assert c['stochastic_condition']['profile'] is None
    det = c['deterministic_condition']
    assert det['temperature'] == 0.0
    assert det['top_k'] == 1


def test_b_selected_dev_only():
    dr = _read('decision_rules.json')
    b = dr['b_selected_rule']
    assert b['selected_from'] == 'DEV_ONLY'
    assert b['eval_hindsight_diagnostic_only'] is True


def test_primary_metrics_have_direction():
    mc = _read('metrics_contract.json')
    pm = mc['prediction_metrics']
    assert pm['primary_metric_id']
    for m in pm['metrics']:
        assert m['direction']
    for m in mc['retention_metrics']['metrics']:
        assert m['direction'] in {'higher','lower','zero','report'}
