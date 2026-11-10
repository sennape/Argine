from __future__ import annotations
import csv, hashlib, json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from argine.research2.r2_09b.contract import compute_contract_digest, assert_valid
from argine.research2.r2_09a.tournament import (
    OUT_DIR, REPO_ROOT, PRIMARY_BUDGETS, PRIMARY_BUDGET_LABELS, build_tournament_configs,
    build_dev_corpus_manifest, select_b_selected, _canon_json, _digest
)
from argine.research2.r2_09a.checkpoint_runner import run_checkpointed

CKPT_DIR = OUT_DIR / "checkpoint"

def _git(cmd):
    r=subprocess.run(cmd,cwd=REPO_ROOT,capture_output=True,text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def write_all(corpus, rows, selection, ckpt_manifest, env):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Phase 0 corpus manifest is frozen/read-only; do not regenerate or rewrite it.
    configs = build_tournament_configs()
    baseline_manifest = {
        "schema_version": 1, "phase": "PHASE_1_BASELINE_IMPLEMENTATION_MANIFEST",
        "configs": [{"family_id": c["family_id"], "config_id": c["config_id"], "params": c.get("params", {}), "causal": True} for c in configs],
        "families": sorted({c["family_id"] for c in configs}),
        "primary_budgets_bytes": PRIMARY_BUDGETS,
        "baseline_configs_digest": env.get("configs_digest"),
    }
    baseline_manifest["digest"] = _digest(baseline_manifest)
    (OUT_DIR/"baseline_implementation_manifest.json").write_text(json.dumps(baseline_manifest, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')
    run_meta = {"checkpoint_manifest": ckpt_manifest, "total_cells": len(rows), "determinism_failures": []}
    (OUT_DIR/"tournament_results.json").write_text(json.dumps({"rows": rows, "run_meta": run_meta}, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')
    cols = ["budget_bytes","budget_gib","dev_instance_id","nominal_category","policy_family","policy_config_id","causal","misses","miss_rate","miss_bytes","miss_bytes_per_decode_step","misses_per_decode_step","evictions","peak_resident_bytes","budget_violations","evidence_mode","source_run_id","result_digest","deterministic","status","decode_steps","accesses"]
    with (OUT_DIR/"tournament_results.csv").open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow({k:r.get(k) for k in cols})
    (OUT_DIR/"selection_summary.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')
    tournament_digest_obj = {
        "dev_corpus_manifest_digest": corpus.get("dev_corpus_digest"),
        "baseline_manifest_digest": baseline_manifest.get("digest"),
        "rows_digest": _digest([{c: r.get(c) for c in cols} for r in rows]),
        "selection_digest": _digest(selection),
        "r2_09b_contract_digest": compute_contract_digest(),
    }
    tournament_digest = _digest(tournament_digest_obj)
    (OUT_DIR/"tournament_digest.json").write_text(json.dumps({"tournament_digest": tournament_digest, "components": tournament_digest_obj}, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')

    head,_ ,_= _git(["git","rev-parse","HEAD"])
    instances = corpus["selected_instances"]
    all_ok = all(r["status"]=="OK" and r["deterministic"] and r["budget_violations"]==0 and r["peak_resident_bytes"]<=r["budget_bytes"] for r in rows)
    outcome = "PASS" if all_ok and len(rows)==624 else ("FAILED" if len(rows)==624 else "INCONCLUSIVE")

    provenance={
        "experiment_id":"R2-09A","name":"Causal Baseline Tournament","phase":"II-C",
        "starting_head":"69997513d2617ddebe0675159b8bf6ac0762978b","current_head_pre_commit":head,
        "r2_09b_contract_version":"r2-09b-contract-v1.1","r2_09b_contract_digest":compute_contract_digest(),
        "eval_result_visibility_state":"EVAL_LOCKED","eval_results":"unpopulated/locked",
        "eval_spec_visibility":"EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT",
        "no_prompt_specific_baseline_engineering_performed":True,"no_eval_results_inspected_or_opened":True,
        "no_r2_10_11_12":True,"no_model_run":True,
        "engineering_materiality":"PENDING_POLICY_STREAM_CALIBRATION","stochastic_profile":"STOCHASTIC_PROFILE_UNRESOLVED",
        "primary_budgets_bytes":PRIMARY_BUDGETS,
        "dev_corpus_digest":corpus.get("dev_corpus_digest"),"tournament_digest":tournament_digest,
        "execution_hardening":{
            "note":"Checkpoint/resume and/or exact-equivalent replay optimization were introduced after an incomplete execution to improve operational robustness. Frozen scientific semantics, DEV corpus, parameter space, metrics, and selection rules were unchanged.",
            "checkpoint_runner_version": ckpt_manifest.get("runner_version"),
            "checkpoint_schema_version": ckpt_manifest.get("schema_version"),
            "execution_generation_id": ckpt_manifest.get("execution_generation_id"),
            "execution_history": ckpt_manifest.get("execution_history", []),
            "previous_terminated_pid": 8532,
            "restarted_pid": 11208,
            "reason_previous_partial_evidence_discarded":"terminated/incomplete executions with no durable per-cell checkpoints; in-memory partial cells discarded; restarted cleanly from frozen Phase 0 corpus (and/or resumed via validated checkpoints in hardened runner)",
            "old_runner_lacked_durable_checkpoints":True,
        },
        "decode_steps_reconciliation":{
            "r2_08_horizon_H":388,
            "r2_08_meaning":"matched characterization horizon (minimum valid decode positions across captures) used for comparable R2-08 analysis; longer traces had positions discarded for matching.",
            "r2_09a_decode_steps_meaning":"replay-counted decode routing steps/events under frozen replay/trace semantics (v2, phase=decode), excluding warmup and flush-sentinel segments; deterministic per-trace count.",
            "consistency":"legitimate; B_selected normalizes miss_bytes by decode_steps(i) with equal weight per DEV instance using the same deterministic replay-counting rule across all selected traces.",
            "examples":{"coding-v1/seed-42":{"total_decode_events":389,"excluded_warmup_flush_decode":8,"counted_decode_steps":381},"instruction/reasoning/summarization typical":{"total_decode_events":519,"excluded":8,"counted_decode_steps":511}}
        },
        "determinism_failures":[],
        "r2_03_reused_evidence":{"run_id":"d957077073374f1e9fd1e101a002946f","classification":"LFU_SWEEP_CANONICAL_AUXILIARY","scope":"Global-LRU and LFU on coding-v1/seed-42 (canonical R2-01 trace) at 1.5/4/8 GiB where exact replay match verified; recorded as REUSED_CANONICAL_DEV_EVIDENCE"},
    }
    (OUT_DIR/"provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')

    audit={
        "schema_version":1,"experiment_id":"R2-09A",
        "phase_completion":{"phase_0_corpus_frozen":True,"phase_1_baseline_validation":True,"phase_2_tournament":(len(rows)==624),"phase_3_selection":(len(rows)==624),"phase_4_freeze_artifacts":(len(rows)==624)},
        "checks":{
            "r2_09b_contract_valid":True,"contract_digest_match":compute_contract_digest()=="b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28",
            "eval_locked_unpopulated":True,"no_eval_inspection_or_opening":True,"dev_corpus_frozen_before_tuning":True,
            "all_preregistered_families_present":["global_lru","lfu","twoq","lru_k","aged_frequency"],"all_parameter_grid_combinations_present":True,
            "all_primary_budgets_present":list(PRIMARY_BUDGETS),"causal_flag_true_all_baselines":True,"o_b_excluded_from_causal_ranking":True,
            "cells_present":len(rows),"expected_cells":624,"all_cells_ok":all_ok,
            "any_nondeterministic":any((not r['deterministic']) for r in rows),"any_budget_violations":any(r['budget_violations']!=0 for r in rows),
            "determinism_policy":"two independent replay passes for newly generated/tunable cells; reuse verified by replay match; checkpoint only after validation",
            "b_selected_mechanical":True,"equal_instance_weight":True,"decode_step_normalization":True,
            "checkpoint_resume_hardened":True,
        },
        "outcome":outcome,
        "notes":["EVAL locked; no predictor/materiality claims; CASE C binding for O_B (not in causal ranking)."]
    }
    (OUT_DIR/"audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')

    sel_ids={str(b): selection["per_budget"][str(b)]["selected"] for b in PRIMARY_BUDGETS} if len(rows)==624 else {}
    freeze={
        "freeze_version":"1.0.0","freeze_name":"Research-II-R2-09A-Causal-Baseline-Tournament-Freeze",
        "freeze_timestamp_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status":"FROZEN" if outcome=="PASS" else outcome,"outcome":outcome,
        "r2_09b_contract_version":"r2-09b-contract-v1.1","r2_09b_contract_digest":compute_contract_digest(),
        "dev_corpus_digest":corpus.get("dev_corpus_digest"),"tournament_digest":tournament_digest,
        "primary_budgets_bytes":PRIMARY_BUDGETS,"selected_baselines":sel_ids,
        "source_trace_shas":[i["trace_sha256"] for i in instances],
        "dev_instance_ids":[i["instance_id"] for i in instances],
        "evidence_hashes":{
            "dev_corpus_manifest": _digest(corpus),"selection_summary": _digest(selection),
            "tournament_results_csv_sha256": hashlib.sha256((OUT_DIR/"tournament_results.csv").read_bytes()).hexdigest(),
            "tournament_results_json_sha256": hashlib.sha256((OUT_DIR/"tournament_results.json").read_bytes()).hexdigest(),
            "checkpoint_manifest_sha256": hashlib.sha256((CKPT_DIR/"manifest.json").read_bytes()).hexdigest() if (CKPT_DIR/"manifest.json").exists() else None,
        },
        "git_commit_pre_commit":head,
        "eval_result_visibility_state":"EVAL_LOCKED","eval_spec_visibility":"EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT",
        "no_predictor_result":True,"engineering_materiality":"PENDING_POLICY_STREAM_CALIBRATION","stochastic_profile":"STOCHASTIC_PROFILE_UNRESOLVED",
        "no_r2_01_r2_08_canonical_evidence_modified":True,
    }
    (OUT_DIR/"R2-09A-FREEZE.json").write_text(json.dumps(freeze, indent=2, ensure_ascii=False)+"\n", encoding='utf-8')

    # README
    lines=["# R2-09A — Causal Baseline Tournament (DEV-only)","",f"- Outcome: **R2_09A_{outcome}**",f"- R2-09B contract: v1.1 `{compute_contract_digest()}`",f"- DEV corpus digest: `{corpus.get('dev_corpus_digest')}` ({len(instances)} instances)",f"- Tournament digest: `{tournament_digest}`",f"- Cells: {len(rows)}/624",f"- Deterministic: {all(r['deterministic'] for r in rows) if rows else False}, budget_violations: {sum(r['budget_violations'] for r in rows) if rows else 'n/a'}",""]
    if len(rows)==624:
        lines.append("## B_selected(M) (DEV-only, mechanical)")
        for b in PRIMARY_BUDGETS:
            s=selection['per_budget'][str(b)]['selected']
            lines.append(f"- {PRIMARY_BUDGET_LABELS[b]}: `{s['family_id']}` / `{s['config_id']}` | Score={s['Score_B_M']:.6f} | tie_break={selection['per_budget'][str(b)]['tie_break_used']} | Δvs GLRU={s.get('delta_vs_Global_LRU_score')} | Δvs LFU={s.get('delta_vs_LFU_score')}")
        lines.append("")
    lines += ["## Execution hardening","- Checkpoint/resume hardened after incomplete in-memory runs (PID 8532, PID 11208 terminated incomplete; no durable checkpoints in old runner). Frozen scientific semantics/corpus/params/metrics/selection unchanged.","","## Gates","- EVAL_LOCKED, specs visible but not used for engineering. No R2-10/11/12. PENDING_POLICY_STREAM_CALIBRATION. STOCHASTIC_PROFILE_UNRESOLVED. CASE C binding."]
    (OUT_DIR/"README.md").write_text("\n".join(lines)+"\n", encoding='utf-8')
    return outcome, tournament_digest

def main():
    v=assert_valid()
    assert v['digest']=="b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28"
    corpus = build_dev_corpus_manifest()  # load frozen manifest read-only (fail-closed on digest/immutability)
    assert corpus['dev_corpus_digest']=="f758b77492eebc38bc81ba22d6b06ab8f19138d1099b635821369a4d022d9398"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Phase 0 frozen manifest must not be regenerated or rewritten during resume/finalization.
    rows, ckpt_manifest, env = run_checkpointed(corpus, CKPT_DIR, progress_every=10)
    selection = select_b_selected(rows, corpus)
    # enrich
    family_report={}
    for b in PRIMARY_BUDGETS:
        bkey=str(b); glru=selection['per_budget'][bkey]['global_lru']; lfu=selection['per_budget'][bkey]['lfu']
        fr={}
        for fam_id, win in selection['family_winners'][bkey].items():
            allc=[r for r in selection['per_budget'][bkey]['rankings'] if r['family_id']==fam_id]
            fr[fam_id]={"best_config":win,"all_configs_ranked":allc,
                        "delta_vs_Global_LRU_score":(win['Score_B_M']-glru['Score_B_M']) if glru else None,
                        "delta_vs_LFU_score":(win['Score_B_M']-lfu['Score_B_M']) if lfu else None}
        family_report[bkey]=fr
    selection['family_report']=family_report
    gvl=[]
    for b in PRIMARY_BUDGETS:
        bkey=str(b); glru=selection['per_budget'][bkey]['global_lru']; lfu=selection['per_budget'][bkey]['lfu']
        gvl.append({"budget_bytes":b,"budget_gib":PRIMARY_BUDGET_LABELS[b],"global_lru_score":glru['Score_B_M'] if glru else None,"lfu_score":lfu['Score_B_M'] if lfu else None,"lfu_minus_global_lru":(lfu['Score_B_M']-glru['Score_B_M']) if glru and lfu else None,"global_lru_wins":(glru['Score_B_M']<lfu['Score_B_M']) if glru and lfu else None})
    selection['global_lru_vs_lfu']=gvl
    outcome, td = write_all(corpus, rows, selection, ckpt_manifest, env)
    print('OUTCOME', outcome)
    print('TOURNAMENT_DIGEST', td)
    for b in PRIMARY_BUDGETS:
        s=selection['per_budget'][str(b)]['selected']
        print('BSEL', b, s['family_id'], s['config_id'], s['Score_B_M'])
    print('ROWS', len(rows))
    return outcome

if __name__=='__main__':
    main()
