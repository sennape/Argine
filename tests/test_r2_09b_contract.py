"""R2-09B v1.1 hardened contract validation tests (preregistration/freeze only).

No model execution, no EVAL capture, no R2-09A experiments.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from argine.research2.r2_09b.contract import (
    CONTRACT_DIGEST_V10,
    CONTRACT_VERSION,
    DEV_WORKLOAD_SPECS,
    PRIMARY_BUDGETS_GIB,
    compute_contract_digest,
    default_contract_dir,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = default_contract_dir()


def _read(name: str):
    return json.loads((CONTRACT_DIR / name).read_text(encoding='utf-8'))


def test_contract_loads_and_validates():
    errors = validate_contract(CONTRACT_DIR)
    assert errors == [], errors


def test_contract_version_and_v1_0_provenance():
    c = _read('contract.json')
    assert c['contract_version'] == CONTRACT_VERSION == 'r2-09b-contract-v1.1'
    am = c['amendment']
    assert am['amends_version'] == 'r2-09b-contract-v1'
    assert am['previous_contract_digest'] == CONTRACT_DIGEST_V10


def test_contract_digest_deterministic_and_recorded():
    d1 = compute_contract_digest(CONTRACT_DIR)
    d2 = compute_contract_digest(CONTRACT_DIR)
    assert d1 == d2
    c = _read('contract.json')
    assert c['contract_digest'] == d1
    freeze = json.loads((ROOT / 'paper/research2/R2-09B-FREEZE.json').read_text(encoding='utf-8'))
    assert freeze['contract_digest'] == d1
    assert freeze['previous_contract_digest_v1_0'] == CONTRACT_DIGEST_V10


def test_eval_results_locked_and_spec_visibility_explicit():
    em = _read('eval_manifest.json')
    assert em['eval_visibility_state'] == 'EVAL_LOCKED'
    assert em.get('eval_results') in (None, [], {})
    assert em.get('opened_utc') is None
    sv = em['spec_visibility']
    assert sv['status'] == 'EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT'
    assert sv['fully_blinded'] is False
    assert sv['result_visibility'] == 'EVAL_RESULT_LOCKED'
    blob = json.dumps(sv).lower()
    assert 'prompt-specific' in blob and 'prohibit' in blob


def test_eval_instance_scope_not_family_generalization():
    em = _read('eval_manifest.json')
    scope = em['design_decision']
    assert scope['option'] == 'A_MINIMAL_FOUR_INSTANCES'
    cs = scope['confirmatory_scope'].lower()
    assert 'instance' in cs
    assert 'family/category-level generalization is unmeasured' in cs
    assert len(em['eval_workload_specs']) == 4
    for s in em['eval_workload_specs']:
        assert s['scope_label'] == 'held-out workload instance'
        assert s.get('nominal_workload_category')


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
    families = {s['nominal_workload_category'] for s in em['eval_workload_specs']}
    assert families == {'coding', 'reasoning', 'summarization', 'instruction'}


def test_primary_hypotheses_unique_instance_scoped():
    dr = _read('decision_rules.json')
    hyps = dr['primary_hypotheses']
    ids = [h['hypothesis_id'] for h in hyps]
    assert len(ids) == len(set(ids))
    assert len(hyps) == 12
    cells = {(h['budget_gib'], h['nominal_workload_category'], h['decoding_condition']) for h in hyps}
    expected = {(b, w, 'deterministic') for b in PRIMARY_BUDGETS_GIB for w in ['coding','reasoning','summarization','instruction']}
    assert cells == expected
    for h in hyps:
        assert h['budget_gib'] in PRIMARY_BUDGETS_GIB
        assert h['evaluation_state'] == 'EVAL_LOCKED'
        assert h.get('eval_result') is None
        assert h['confirmatory'] is True
        assert h['workload_instance_id'].endswith('-eval-v1')
        assert 'instance' in h['confirmatory_scope'].lower()
        assert h.get('k_tl_rule')


def test_budgets_and_16gib_not_primary():
    c = _read('contract.json')
    assert tuple(c['primary_budgets_gib']) == PRIMARY_BUDGETS_GIB
    assert 16.0 not in c['primary_budgets_gib'] and 16 not in c['primary_budgets_gib']
    assert '16.0' in json.dumps(c['excluded_primary_budgets']) or '16' in json.dumps(c['excluded_primary_budgets'])


def test_b_selected_aggregation_frozen():
    dr = _read('decision_rules.json')
    b = dr['b_selected_rule']
    assert b['selected_from'] == 'DEV_ONLY'
    assert b['eval_hindsight_diagnostic_only'] is True
    agg = b['aggregation']
    assert 'miss_bytes(B,M,i) / decode_steps(i)' in agg['formula']
    assert 'equal weight' in agg['weights'].lower()
    assert agg['normalization_denominator']
    assert agg['tie_breaking']


def test_primary_recall_k_tl_cutoff_fixed():
    pc = _read('prediction_contract.json')
    cutoff = pc['primary_metric_cutoff']
    assert 'router requested cardinality' in cutoff['k_definition']
    assert 'exactly k_tl' in cutoff['predictor_output_cardinality']
    assert cutoff['ranking_cutoff'] == 'top-k_tl by score'
    assert cutoff['tie_breaking']
    assert cutoff['variable_cardinality']
    mc = _read('metrics_contract.json')
    pm = next(m for m in mc['prediction_metrics']['metrics'] if m['primary'])
    assert pm['metric_id'] == 'future_requested_expert_recall_at_k'
    assert 'exactly k_tl' in pm['fixed_output_cardinality']


def test_cross_layer_causal_boundary_explicit():
    pc = _read('prediction_contract.json')
    its = pc['input_time_semantics']
    assert its['causal_boundary_name'] == 'intrastep_cross_layer_causal_context'
    allowed = json.dumps(its['allowed']).lower()
    forbidden = json.dumps(its['forbidden']).lower()
    assert 'layers < l' in allowed or 'layers < l of the same step t' in allowed
    assert 'same step t' in allowed
    assert 'layer > l' in forbidden
    assert 'target e_{t,l}' in forbidden or 'target e_{t,l} itself' in forbidden


def test_baseline_semantics_complete_no_deferral():
    bs = _read('baseline_space.json')
    blob = json.dumps(bs)
    assert 'exact formula frozen in implementation spec before R2-09A' not in blob
    fams = {f['family_id']: f for f in bs['baseline_families']}
    for req in ['global_lru','lfu','twoq','lru_k','aged_frequency']:
        assert req in fams
        assert fams[req]['causal'] is True
    twoq = fams['twoq']['frozen_semantics']
    assert 'byte_partition' in twoq and 'a1out_ghost' in twoq and 'on_miss_key_in_a1out' in twoq
    assert fams['lfu']['hyperparameters']['fixed']['tie_break'] or fams['lfu'].get('frozen_semantics')
    for f in bs['baseline_families']:
        hp = f.get('hyperparameters', {})
        if hp.get('tunable'):
            assert hp.get('search_space'), f['family_id']


def test_case_c_and_recovery_semantics_unchanged():
    c = _read('contract.json')
    cc = c['case_c']
    assert cc['o_b_proven_byte_optimal'] is False
    rob = cc['recovery_of_belady_gap']
    assert rob['signed'] is True and rob['allows_negative'] is True and rob['allows_greater_than_one'] is True


def test_exp025_and_threshold_unchanged():
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
    assert det['temperature'] == 0.0 and det['top_k'] == 1


def test_primary_metrics_have_direction():
    mc = _read('metrics_contract.json')
    pm = mc['prediction_metrics']
    assert pm['primary_metric_id']
    for m in pm['metrics']:
        assert m['direction']
    for m in mc['retention_metrics']['metrics']:
        assert m['direction'] in {'higher','lower','zero','report'}
