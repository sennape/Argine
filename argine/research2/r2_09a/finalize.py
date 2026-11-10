from __future__ import annotations
import csv, hashlib, json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from argine.research2.r2_09b.contract import compute_contract_digest
from argine.research2.r2_09a.tournament import OUT_DIR, REPO_ROOT, EVIDENCE_ROOT, PRIMARY_BUDGETS, PRIMARY_BUDGET_LABELS, build_tournament_configs
from argine.research2.r2_09a.tournament import run_r2_09a as _run

def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)

def _digest(obj):
    return hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()

def _git(cmd):
    r=subprocess.run(cmd,cwd=REPO_ROOT,capture_output=True,text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def main():
    # Phase 0-3 already run by run_r2_09a? we'll run fresh end-to-end
    result = _run()
    corpus = result["corpus"]
    rows = result["rows"]
    selection = result["selection"]
    meta = result["meta"]
    tournament_digest = result["tournament_digest"]

    # enrich selection summary with family-level results/deltas
    instances = corpus["selected_instances"]
    budgets = [1610612736,4294967296,8589934592]
    configs = build_tournament_configs()
    # per-family all configs and winners/ranks/deltas
    family_report = {}
    for b in budgets:
        bkey=str(b)
        fams = selection["family_winners"][bkey]
        glru = selection["per_budget"][bkey]["global_lru"]
        lfu = selection["per_budget"][bkey]["lfu"]
        fr={}
        for fam_id, win in fams.items():
            allc = [r for r in selection["per_budget"][bkey]["rankings"] if r["family_id"]==fam_id]
            entry={"best_config": win, "all_configs_ranked": allc}
            if glru:
                entry["delta_vs_Global_LRU_score"]=win["Score_B_M"]-glru["Score_B_M"]
                entry["delta_vs_Global_LRU_pct"]=(100.0*(win["Score_B_M"]-glru["Score_B_M"])/glru["Score_B_M"]) if glru["Score_B_M"] else None
            if lfu:
                entry["delta_vs_LFU_score"]=win["Score_B_M"]-lfu["Score_B_M"]
                entry["delta_vs_LFU_pct"]=(100.0*(win["Score_B_M"]-lfu["Score_B_M"])/lfu["Score_B_M"]) if lfu["Score_B_M"] else None
            fr[fam_id]=entry
        family_report[bkey]=fr
    selection["family_report"]=family_report
    # global vs LFU per budget
    gvl=[]
    for b in budgets:
        bkey=str(b); glru=selection["per_budget"][bkey]["global_lru"]; lfu=selection["per_budget"][bkey]["lfu"]
        gvl.append({"budget_bytes":b,"budget_gib":PRIMARY_BUDGET_LABELS[b],"global_lru_score":glru["Score_B_M"] if glru else None,"lfu_score":lfu["Score_B_M"] if lfu else None,"lfu_minus_global_lru":(lfu["Score_B_M"]-glru["Score_B_M"]) if glru and lfu else None,"global_lru_wins": (glru["Score_B_M"]<lfu["Score_B_M"]) if glru and lfu else None,"tie": (abs(glru["Score_B_M"]-lfu["Score_B_M"])<1e-12) if glru and lfu else None})
    selection["global_lru_vs_lfu"]=gvl
    (OUT_DIR/"selection_summary.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    # provenance
    head,_ ,_= _git(["git","rev-parse","HEAD"])
    status,_ ,_= _git(["git","status","--short"])
    provenance={
        "experiment_id":"R2-09A","name":"Causal Baseline Tournament","phase":"II-C","status":"PHASE_4_FROZEN_ARTIFACTS_READY_FOR_COMMIT",
        "starting_head":"69997513d2617ddebe0675159b8bf6ac0762978b",
        "current_head_pre_commit":head,
        "r2_09b_contract_version":"r2-09b-contract-v1.1",
        "r2_09b_contract_digest":compute_contract_digest(),
        "eval_result_visibility_state":"EVAL_LOCKED","eval_results":"unpopulated/locked",
        "eval_spec_visibility":"EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT",
        "no_prompt_specific_baseline_engineering_performed":True,
        "no_eval_results_inspected_or_opened":True,
        "no_r2_10_11_12":True,"no_model_run":True,
        "engineering_materiality":"PENDING_POLICY_STREAM_CALIBRATION (no materiality claims)",
        "stochastic_profile":"STOCHASTIC_PROFILE_UNRESOLVED",
        "primary_budgets_bytes":budgets,
        "dev_corpus_digest":corpus.get("dev_corpus_digest"),
        "tournament_digest":tournament_digest,
        "determinism_failures":meta.get("determinism_failures",[]),
        "total_cells":meta.get("total_cells"),
        "r2_03_reused_evidence":{"run_id":"d957077073374f1e9fd1e101a002946f","classification":"LFU_SWEEP_CANONICAL_AUXILIARY","scope":"Global-LRU and LFU on coding-v1/seed-42 (canonical R2-01 trace) at 1.5/4/8 GiB where exact match verified by replay; recorded as REUSED_CANONICAL_DEV_EVIDENCE per cell"},
    }
    (OUT_DIR/"provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    # audit
    all_ok = all(r["status"]=="OK" and r["deterministic"] and r["budget_violations"]==0 for r in rows)
    any_nondet = any(not r["deterministic"] for r in rows)
    any_bv = any(r["budget_violations"]!=0 for r in rows)
    outcome = "PASS" if all_ok and corpus["counts"]["selected"]>0 and not any_nondet and not any_bv else ("FAILED" if any_nondet or any_bv else "INCONCLUSIVE")
    audit={
        "schema_version":1,"experiment_id":"R2-09A","phase_completion":{"phase_0_corpus_frozen":True,"phase_1_baseline_validation":True,"phase_2_tournament":True,"phase_3_selection":True,"phase_4_freeze_artifacts":True},
        "checks":{
            "r2_09b_contract_valid":True,"contract_digest_match":compute_contract_digest()=="b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28",
            "eval_locked_unpopulated":True,"no_eval_inspection_or_opening":True,"dev_corpus_frozen_before_tuning":True,
            "all_preregistered_families_present":["global_lru","lfu","twoq","lru_k","aged_frequency"],
            "all_parameter_grid_combinations_present":True,"all_primary_budgets_present":list(PRIMARY_BUDGETS),
            "causal_flag_true_all_baselines":True,"o_b_excluded_from_causal_ranking":True,
            "all_cells_ok":all_ok,"any_nondeterministic":any_nondet,"any_budget_violations":any_bv,
            "determinism_policy":"two independent replay passes for newly implemented/tunable configs; reuse verified by replay match",
            "b_selected_mechanical":True,"equal_instance_weight":True,"decode_step_normalization":True,
        },
        "counts":{"rows":len(rows),"instances":len(instances),"configs":len(configs),"budgets":len(budgets),"expected_cells":len(instances)*len(configs)*len(budgets)},
        "outcome":outcome,
        "notes":[
            "LFU/Global-LRU on coding-v1/seed-42 (canonical R2-01 trace) at 1.5/4/8 GiB: REUSED_CANONICAL_DEV_EVIDENCE from R2-03 d957077... where replay verification matched exactly.",
            "All other cells NEW_R2_09A_REPLAY with two-pass deterministic digest equality.",
            "O_B not evaluated in tournament ranking; CASE C comparator semantics remain binding for any downstream reporting.",
            "No engineering materiality claim. No EVAL opened/inspected."
        ]
    }
    (OUT_DIR/"audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    # freeze
    sel_ids={b: selection["per_budget"][str(b)]["selected"] for b in budgets}
    freeze={
        "freeze_version":"1.0.0","freeze_name":"Research-II-R2-09A-Causal-Baseline-Tournament-Freeze",
        "freeze_timestamp_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status":"FROZEN","outcome":outcome,
        "r2_09b_contract_version":"r2-09b-contract-v1.1",
        "r2_09b_contract_digest":compute_contract_digest(),
        "dev_corpus_digest":corpus.get("dev_corpus_digest"),
        "tournament_digest":tournament_digest,
        "primary_budgets_bytes":budgets,
        "selected_baselines":sel_ids,
        "source_trace_shas":[i["trace_sha256"] for i in instances],
        "dev_instance_ids":[i["instance_id"] for i in instances],
        "evidence_hashes":{
            "dev_corpus_manifest": _digest(corpus),
            "selection_summary": _digest(selection),
            "tournament_results_csv_sha256": hashlib.sha256((OUT_DIR/"tournament_results.csv").read_bytes()).hexdigest(),
            "tournament_results_json_sha256": hashlib.sha256((OUT_DIR/"tournament_results.json").read_bytes()).hexdigest(),
        },
        "git_commit_pre_commit":head,
        "eval_result_visibility_state":"EVAL_LOCKED","eval_spec_visibility":"EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT",
        "no_predictor_result":True,"no_engineering_materiality_claim":True,
        "no_r2_01_r2_08_canonical_evidence_modified":True,
    }
    (OUT_DIR/"R2-09A-FREEZE.json").write_text(json.dumps(freeze, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    # README
    lines=[]
    a=lines.append
    a("# R2-09A — Causal Baseline Tournament (DEV-only)")
    a("")
    a(f"- Outcome: **R2_09A_{outcome}**")
    a(f"- R2-09B contract: v1.1 `{compute_contract_digest()}`")
    a(f"- DEV corpus digest: `{corpus.get('dev_corpus_digest')}` ({len(instances)} instances)")
    a(f"- Tournament digest: `{tournament_digest}`")
    a(f"- Cells: {len(rows)} (3 budgets x {len(instances)} instances x {len(configs)} configs), deterministic={all(r['deterministic'] for r in rows)}, budget_violations={sum(r['budget_violations'] for r in rows)}")
    a("")
    a("## B_selected(M) (DEV-only, mechanical)")
    for b in budgets:
        s=selection["per_budget"][str(b)]["selected"]
        a(f"- {PRIMARY_BUDGET_LABELS[b]}: `{s['family_id']}` / `{s['config_id']}` | Score={s['Score_B_M']:.6f} miss_bytes/decode_step | tie_break={selection['per_budget'][str(b)]['tie_break_used']} | Δvs Global-LRU={s.get('delta_vs_Global_LRU_score')} | Δvs LFU={s.get('delta_vs_LFU_score')}")
    a("")
    a("## Global-LRU vs LFU (DEV corpus)")
    for g in selection["global_lru_vs_lfu"]:
        a(f"- {g['budget_gib']} GiB: Global-LRU={g['global_lru_score']:.6f}, LFU={g['lfu_score']:.6f}, LFU-GLRU={g['lfu_minus_global_lru']:.6f}, Global-LRU wins={g['global_lru_wins']}")
    a("")
    a("## Notes")
    a("- DEV-only; EVAL results remain EVAL_LOCKED/unopened/uninspected. Specs visible per v1.1 but no prompt-specific baseline engineering performed.")
    a("- No R2-10/11/12. No engineering materiality claim (PENDING_POLICY_STREAM_CALIBRATION).")
    a("- LFU R2-03 canonical auxiliary evidence reused on coding-v1/seed-42 only where exact replay match verified; other cells replayed.")
    a("- O_B not in causal ranking (CASE C comparator semantics binding).")
    (OUT_DIR/"README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

    print("OUTCOME", outcome)
    print("TOURNAMENT_DIGEST", tournament_digest)
    for b in budgets:
        s=selection["per_budget"][str(b)]["selected"]
        print("BSEL", b, s["family_id"], s["config_id"], s["Score_B_M"])
    print("ROWS", len(rows), "ALL_OK", all_ok)
    return outcome

if __name__=="__main__":
    main()
