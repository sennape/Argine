"""Verify every numerical claim of research3.tex against the frozen evidence and (re)write SOURCE_MAP.md.

Each registry entry recomputes the value from a frozen artifact, formats it exactly as printed, and requires that the printed
string occurs in the manuscript or its generated tables. Exits non-zero on any mismatch. Read-only with respect to evidence.
"""
from __future__ import annotations
import glob, hashlib, json, subprocess, sys
from fractions import Fraction
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import frozen

PAPER = HERE.parent; REPO = frozen.REPO


def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()


def commit_of(rel: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h", "--", rel], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def freeze_id(d: str) -> str:
    p = REPO / d / "FREEZE.json"
    if not p.exists(): return "n/a"
    fz = json.loads(p.read_text()); return fz.get("freeze_sha256") or f"FREEZE.json file {sha(p)}"


SRC = {  # key: (stage, evidence dir whose FREEZE binds the artifact, artifact path)
    "A3": ("R3-03A", "experiments/R3-03A-runtime-semantic-conformance", "experiments/R3-03A-runtime-semantic-conformance/CONFORMANCE_RESULT.json"),
    "AUD": ("R3-03 event audit", "experiments/R3-03-event-semantics-audit", "experiments/R3-03-event-semantics-audit/README.md"),
    "MB": ("R3-03B microbench", "experiments/R3-03B-microbench-evidence", "experiments/R3-03B-microbench-evidence/MICROBENCH_RESULT.json"),
    "B3": ("R3-03B final", "experiments/R3-03B-final-evidence", "experiments/R3-03B-final-evidence/FINAL_RESULT.json"),
    "R4C": ("R3-04", "experiments/R3-04-final-evidence", "experiments/R3-04-closure/CLOSURE.json"),
    "AM": ("R3-04 amendment", "experiments/R3-04-campaign-efficiency-amendment", "experiments/R3-04-campaign-efficiency-amendment/AMENDMENT.json"),
    "P0": ("R3-05 Phase 0", "experiments/R3-05-economic-retention", "experiments/R3-05-economic-retention/phase0.py (envelope())"),
    "R1": ("R3-05 R1", "experiments/R3-05-economic-retention/r1-fidelity", "experiments/R3-05-economic-retention/r1-fidelity/FIDELITY_RESULTS.json"),
    "STR": ("R3-05 R1", "experiments/R3-05-economic-retention/r1-fidelity", "experiments/R3-05-economic-retention/r1-fidelity/CANONICAL_STREAM.jsonl"),
    "R2": ("R3-05 R2", "experiments/R3-05-economic-retention/r2-belady-ceiling", "experiments/R3-05-economic-retention/r2-belady-ceiling/RESULTS.json"),
    "R3": ("R3-05 R3", "experiments/R3-05-economic-retention/r3-ces-pp-curve", "experiments/R3-05-economic-retention/r3-ces-pp-curve/{CES_RESULTS,K2_DECISION,K3_DECISION}.json"),
    "R4": ("R3-05 R4", "experiments/R3-05-economic-retention/r4-nue-candidate-gate", "experiments/R3-05-economic-retention/r4-nue-candidate-gate/{EVAL_RESULTS,HEADROOM_CAPTURE,K4_DECISION,CANDIDATE_SELECTION}.json"),
    "P2": ("Preceding study (Research 2) paper", "paper/research2", "paper/research2/manuscript.tex (abstract)"),
    "L2": ("Preceding study (Research 2) claim ledger", "paper/research2", "paper/research2/claims/claim_ledger.md"),
}


MAPPING = [  # reader-facing name, repository ID, artifact directory (glob relative to the repository root)
    ("Runtime Semantic Equivalence", "R3-03A", "experiments/R3-03A-runtime-semantic-conformance"),
    ("Predictor Cost Attribution", "R3-03B", "experiments/R3-03B-final-evidence"),
    ("Real-Runtime Retention Evaluation", "R3-04", "experiments/R3-04-final-evidence"),
    ("Prospective Predictive-Retention Falsification", "R3-05 Phase 0", "experiments/R3-05-economic-retention"),
    ("Offline Replay Fidelity", "R3-05 R1", "experiments/R3-05-economic-retention/r1-fidelity"),
    ("Clairvoyant Retention Headroom", "R3-05 R2", "experiments/R3-05-economic-retention/r2-belady-ceiling"),
    ("Current-Event Shielding", "R3-05 R3", "experiments/R3-05-economic-retention/r3-ces-pp-curve"),
    ("Causal Next-Use Eviction", "R3-05 R4", "experiments/R3-05-economic-retention/r4-nue-candidate-gate"),
    ("Present-study closure", "R3-final-closure", "experiments/R3-final-closure"),
    ("Oracle Trace Contract and Replay Validation", "R2-01", "experiments/R2-01-*"),
    ("Oracle vs Retention Baselines", "R2-02", "experiments/R2-02-*"),
    ("Expert-Memory Budget Sensitivity", "R2-03", "experiments/R2-03-*"),
    ("Recoverable-Margin Analysis", "R2-04", "experiments/R2-04-*"),
    ("Expert Frequency and Concentration", "R2-05", "experiments/R2-05-*"),
    ("Routing Transition Structure", "R2-06", "experiments/R2-06-*"),
    ("Reuse Distance and Working-Set Structure", "R2-07", "experiments/R2-07-*"),
    ("Routing Drift and Workload Dependence", "R2-08", "experiments/R2-08-*"),
    ("Prediction Benchmark", "R2-09", "experiments/R2-09-*"),
    ("Causal Working-Set Predictor", "R2-10", "experiments/R2-10-*"),
    ("Retention Feasibility Bridge", "R2-BRIDGE-01", "experiments/R2-BRIDGE-01-*"),
    ("Prediction-Driven Retention", "R2-11", "experiments/R2-11-*"),
    ("Real-Runtime Decode Throughput", "R2-12", "experiments/R2-12-*"),
]


def entries(F):
    c = F["closure"]; p15, p8 = c["primary_1.5GiB"], c["control_8GiB_descriptive"]; a = p15["arm_stats"]
    mb = F["mb"]["coefficients"]; b3 = F["b3"]; a3 = F["a3"]["event_counts"]; rows = {r["name"]: r for r in F["rows"]}
    A, PP, CES, LO, X, BEL = (rows[k] for k in ("A (b_selected)", "PP (predicted_protect)", "CES", "NUE_LO", "NUE_X (LEX)", "Belady-style oracle"))
    am = json.loads((REPO / "experiments/R3-04-campaign-efficiency-amendment/AMENDMENT.json").read_text())
    r1 = F["r1"]["replay"]; kl = F["kappa_low"]
    sizes = sorted({json.loads(l)["bytes"] for l in (REPO / "experiments/R3-05-economic-retention/r1-fidelity/CANONICAL_STREAM.jsonl").open()})
    f2 = lambda x: f"{x:.2f}"; ci = lambda q: f"[{q['lo']:.2f}, {q['hi']:.2f}]".replace("-", "-")
    thou = lambda n: f"{n:,}".replace(",", "{,}")
    p2 = (REPO / "paper/research2/manuscript.tex").read_text(encoding="utf-8")
    l2 = (REPO / "paper/research2/claims/claim_ledger.md").read_text(encoding="utf-8")
    E = [
        ("old policy calls per decode token", "A3", "event_counts.old_runtime_policy_calls_per_decode_token", "144 policy calls per decode token", f"{a3['old_runtime_policy_calls_per_decode_token']} policy calls per decode token"),
        ("corrected policy calls per decode token", "A3", "event_counts.policy_calls_per_decode_token", "where the reference defines 48", f"where the reference defines {a3['policy_calls_per_decode_token']}"),
        ("semantic events per launch", "A3", "event_counts.policy_event_rows_per_launch", "24{,}672", thou(a3["policy_event_rows_per_launch"])),
        ("passes", "A3", "event_counts.passes", "over 514 passes", f"over {a3['passes']} passes"),
        ("old-runtime recorded events (4 GiB B trace)", "AUD", "README line 7: 74,016 actual vs 24,672 semantic", "74{,}016", thou(3 * a3["policy_event_rows_per_launch"]) if "74,016" in (REPO / SRC["AUD"][2]).read_text() else "MISSING"),
        ("microbench b_selected per call", "MB", "coefficients.c_call_ns.b_selected.us", "8.80~$\\mu$s", f"{mb['c_call_ns']['b_selected']['us']:.2f}~$\\mu$s"),
        ("microbench sham per call", "MB", "coefficients.c_call_ns.predicted_protect_sham.us", "132.48~$\\mu$s", f"{mb['c_call_ns']['predicted_protect_sham']['us']:.2f}~$\\mu$s"),
        ("microbench full policy per call", "MB", "coefficients.c_call_ns.predicted_protect.us", "134.28~$\\mu$s", f"{mb['c_call_ns']['predicted_protect']['us']:.2f}~$\\mu$s"),
        ("predictor component per call", "MB", "coefficients.c_predictor.us_per_call", "123.68~$\\mu$s", f"{mb['c_predictor']['us_per_call']:.2f}~$\\mu$s"),
        ("predictor ms/token at 48 calls", "MB", "coefficients.c_predictor.ms_per_token_at_48_calls", "5.94~ms/token", f"{mb['c_predictor']['ms_per_token_at_48_calls']:.2f}~ms/token"),
        ("R3-03B wall overhead O", "B3", "mean_O_ms_per_token", "$O = 17.466$", f"$O = {b3['mean_O_ms_per_token']:.3f}$"),
        ("R3-03B in-situ policy contrast", "B3", "mean_P_ms_per_token", "$\\Pi = 19.354$", f"$\\Pi = {b3['mean_P_ms_per_token']:.3f}$"),
        ("rho", "B3", "rho", "$\\rho = 0.9024$", f"$\\rho = {b3['rho']:.4f}$"),
        ("rho Fieller CI", "B3", "fieller.lo/hi", "$[0.8760, 0.9287]$", f"$[{b3['fieller']['lo']:.4f}, {b3['fieller']['hi']:.4f}]$"),
        ("R3-03B blocks/launches", "B3", "n_blocks, counted_launches", "20 ABBA blocks (80 launches", f"{b3['n_blocks']} ABBA blocks ({b3['counted_launches']} launches"),
        ("in-situ over microbench", "B3", "performance.in_situ_over_microbench", "3.26$\\times$", f"{b3['performance']['in_situ_over_microbench']:.2f}$\\times$"),
        ("A ms/token 1.5 GiB", "R4C", "primary_1.5GiB.arm_stats.A.ms_per_token.mean", "A ran at 526.97", f"A ran at {a['A']['ms_per_token']['mean']:.2f}"),
        ("A tok/s", "R4C", "primary_1.5GiB.arm_stats.A.tokens_per_second.mean", "(1.8992 tok/s)", f"({a['A']['tokens_per_second']['mean']:.4f} tok/s)"),
        ("S ms/token", "R4C", "primary_1.5GiB.arm_stats.S.ms_per_token.mean", "S at 541.56", f"S at {a['S']['ms_per_token']['mean']:.2f}"),
        ("P ms/token", "R4C", "primary_1.5GiB.arm_stats.P.ms_per_token.mean", "P at 542.80", f"P at {a['P']['ms_per_token']['mean']:.2f}"),
        ("S-A", "R4C", "primary_1.5GiB.S_minus_A", "$+14.59$~ms/token (95\\% CI $[9.87, 19.31]$)", f"$+{p15['S_minus_A']['mean']:.2f}$~ms/token (95\\% CI ${ci(p15['S_minus_A'])}$)"),
        ("P-S", "R4C", "primary_1.5GiB.P_minus_S", "$+1.24$~ms/token with an interval containing zero ($[-2.84, 5.32]$)", f"$+{p15['P_minus_S']['mean']:.2f}$~ms/token with an interval containing zero (${ci(p15['P_minus_S'])}$)"),
        ("P-A", "R4C", "primary_1.5GiB.P_minus_A_ms_per_token", "$+15.83$~ms/token (95\\% CI $[10.46, 21.19]$)", f"$+{p15['P_minus_A_ms_per_token']['mean']:.2f}$~ms/token (95\\% CI ${ci(p15['P_minus_A_ms_per_token'])}$)"),
        ("abstract P-A", "R4C", "primary_1.5GiB.P_minus_A_ms_per_token", "by 15.83~ms/token (95\\% CI $[10.46, 21.19]$", f"by {p15['P_minus_A_ms_per_token']['mean']:.2f}~ms/token (95\\% CI ${ci(p15['P_minus_A_ms_per_token'])}$"),
        ("throughput change", "R4C", "arm_stats.{P,A}.tokens_per_second.mean (derived)", "$-2.93\\%$", f"$-{abs(100 * (a['P']['tokens_per_second']['mean'] / a['A']['tokens_per_second']['mean'] - 1)):.2f}\\%$"),
        ("8 GiB S-A", "R4C", "control_8GiB_descriptive.S_minus_A", "$S-A = +19.67$~ms/token, $[17.68, 21.66]$", f"$S-A = +{p8['S_minus_A']['mean']:.2f}$~ms/token, ${ci(p8['S_minus_A'])}$"),
        ("8 GiB P-S", "R4C", "control_8GiB_descriptive.P_minus_S", "$P-S = +0.45$, $[-1.25, 2.16]$", f"$P-S = +{p8['P_minus_S']['mean']:.2f}$, ${ci(p8['P_minus_S'])}$"),
        ("primary blocks", "R4C", "primary_1.5GiB.P_minus_A_ms_per_token.n_blocks", "18 blocks at 1.5~GiB", f"{p15['P_minus_A_ms_per_token']['n_blocks']} blocks at 1.5~GiB"),
        ("launches", "AM", "design.physical_launches / counted_launches", "146 physical and 144 counted launches", f"{am['design']['physical_launches']} physical and {am['design']['counted_launches']} counted launches"),
        ("kappa", "P0", "derived.kappa_ms_per_MB_point", "0.7584~\\mathrm{ms/MB}", f"{F['kappa']:.4f}~\\mathrm{{ms/MB}}"),
        ("kappa_low", "P0", "derived.kappa_ms_per_MB_low", "0.3792~\\mathrm{ms/MB}", f"{kl:.4f}~\\mathrm{{ms/MB}}"),
        ("A decode misses replay", "R1", "replay.A.replay.decode.misses", "106{,}491 decode misses", f"{thou(r1['A']['replay']['decode']['misses'])} decode misses"),
        ("PP decode misses replay", "R1", "replay.PP.replay.decode.misses", "106{,}390 (207.793)", f"{thou(r1['PP']['replay']['decode']['misses'])} ({r1['PP']['replay']['decode']['misses'] / 512:.3f})"),
        ("A misses/token", "R2", "budgets.1.5.A.decode.misses / 512", "(207.990~misses/token)", f"({A['misses'] / 512:.3f}~misses/token)"),
        ("Belady misses/token", "R2", "A misses - mechanism.misses_avoided_decode, /512", "from 207.990 to 123.395 per token", f"from {A['misses'] / 512:.3f} to {BEL['misses'] / 512:.3f} per token"),
        ("Belady miss reduction", "R2", "mechanism.misses_avoided_decode / A misses", "($-40.673\\%$)", f"($-{100 * F['bm'] / A['misses']:.3f}\\%$)"),
        ("Belady byte reduction", "R2", "mechanism.miss_bytes_avoided_decode / A miss bytes", "miss bytes by 40.653\\%", f"miss bytes by {100 * F['bb'] / A['miss_bytes']:.3f}\\%"),
        ("Belady misses avoided", "R2", "mechanism.misses_avoided_decode", "43{,}313 misses", f"{thou(F['bm'])} misses"),
        ("Belady bytes avoided", "R2", "mechanism.miss_bytes_avoided_decode", "124{,}509{,}708{,}288 bytes", f"{thou(F['bb'])} bytes"),
        ("Belady headroom per token", "R2", "derived /512 /1e6", "(243.18~MB/token)", f"({F['bb'] / 512 / 1e6:.2f}~MB/token)"),
        ("Belady ceiling", "R4", "HEADROOM_CAPTURE.conservative_ceiling_ms_per_token", "92.216~ms/token", f"{F['hc']['conservative_ceiling_ms_per_token']:.3f}~ms/token"),
        ("A/CES/PP MB per token", "R3", "CES_RESULTS {A,CES,PP_full}.decode.miss_bytes /512", "from 598.198 to 596.840~MB/token against 597.639", f"from {float(A['bytes_pt']) / 1e6:.3f} to {float(CES['bytes_pt']) / 1e6:.3f}~MB/token against {float(PP['bytes_pt']) / 1e6:.3f}"),
        ("CES avoided/capture", "R3", "derived from CES_RESULTS", "CES avoids 245 misses and 0.559\\%", f"CES avoids {CES['misses_avoided']} misses and {100 * float(CES['cap_bytes']):.3f}\\%"),
        ("PP avoided/capture", "R3", "derived from CES_RESULTS", "PP 101 misses and 0.230\\%", f"PP {PP['misses_avoided']} misses and {100 * float(PP['cap_bytes']):.3f}\\%"),
        ("CES/PP ratio", "R3", "K2_DECISION.ratio", "2.43$\\times$ PP's byte benefit", f"{F['k2']['ratio']:.2f}$\\times$ PP's byte benefit"),
        ("K3 retained at N=10", "R3", "K3_DECISION.retained", "62.2\\%", f"{100 * F['k3']['retained']:.1f}\\%"),
        ("NUE_LO", "R4", "EVAL_RESULTS.raw.NUE_LO.decode.miss_bytes /512; HEADROOM_CAPTURE", "586.573~MB/token (4.78\\% of the byte headroom, $V = 4.41$", f"{float(LO['bytes_pt']) / 1e6:.3f}~MB/token ({100 * float(LO['cap_bytes']):.2f}\\% of the byte headroom, $V = {LO['gross_ms']:.2f}$"),
        ("NUE_X", "R4", "EVAL_RESULTS.raw.NUE_X.decode.miss_bytes /512; HEADROOM_CAPTURE", "576.811~MB/token (8.79\\%, $V = 8.11$", f"{float(X['bytes_pt']) / 1e6:.3f}~MB/token ({100 * float(X['cap_bytes']):.2f}\\%, $V = {X['gross_ms']:.2f}$"),
        ("NUE_X vs PP capture ratio", "R4", "derived bytes avoided NUE_X / PP", "about 38$\\times$", f"about {round(X['bytes_avoided'] / PP['bytes_avoided'])}$\\times$"),
        ("NUE_LO/NUE_X", "R4", "K5_DECISION.ratio", "54.4\\%", f"{100 * LO['bytes_avoided'] / X['bytes_avoided']:.1f}\\%"),
        ("K4 bytes floor", "R4", "derived 10/kappa_low", "= 26.37$~MB/token", f"= {10 / kl:.2f}$~MB/token"),
        ("K4 capture floor", "R4", "derived (10/kappa_low)/headroom", "(10.84\\%)", f"({100 * 10 / kl / (F['bb'] / 512 / 1e6):.2f}\\%)"),
        ("PP gross value", "R3", "derived Eq. 2", "(0.21~ms/token", f"({PP['gross_ms']:.2f}~ms/token"),
        ("PP value vs R3-04 cost", "R3/R4C", "derived 14.59/0.21", "about 70 times smaller", f"about {round(p15['S_minus_A']['mean'] / PP['gross_ms'], -1):.0f} times smaller"),
        ("expert sizes", "STR", "distinct bytes values", "about 2.65 and 3.06~MB", f"about {sizes[0] / 1e6:.2f} and {sizes[1] / 1e6:.2f}~MB"),
        ("R2 recall", "P2", "abstract", "0.449817 vs.\\ 0.401473, $+12.04\\%$", "0.449817 vs.\\ 0.401473, $+12.04\\%$" if all(s in p2 for s in ("0.449817", "0.401473", "+12.04")) else "MISSING"),
        ("R2 R-12", "P2", "abstract", "$-0.171721$ tokens/s", "$-0.171721$ tokens/s" if "0.171721" in p2 else "MISSING"),
        ("prev. study 4 GiB gap", "P2", "abstract", "58.59\\% fewer miss bytes", "58.59\\% fewer miss bytes" if "58.59" in p2 else "MISSING"),
        ("prev. study budget gaps", "L2", "claim 4", "44.11\\% at 1.5~GiB, 70.66\\% at 8~GiB, none at 16~GiB", "44.11\\% at 1.5~GiB, 70.66\\% at 8~GiB, none at 16~GiB" if all(x in l2 for x in ("1.5 GiB = 44.11%", "8 GiB = 70.66%", "16 GiB = 0.00%")) else "MISSING"),
        ("prev. study effective expert fraction", "P2", "abstract", "median effective expert fraction 0.514", "median effective expert fraction 0.514" if "$0.514$" in p2 else "MISSING"),
        ("prev. study NMI", "P2", "abstract", "median same-layer NMI 0.099", "median same-layer NMI 0.099" if "$0.099$" in p2 else "MISSING"),
        ("prev. study reuse vs null", "P2", "abstract", "immediate reuse 0.426 vs.\\ 0.187", "immediate reuse 0.426 vs.\\ 0.187" if "$0.426$ vs" in p2 and "$0.187$" in p2 else "MISSING"),
        ("prev. study drift ratio", "P2", "abstract", "($2.21\\times$)", "($2.21\\times$)" if "2.21\\times" in p2 else "MISSING"),
        ("prev. study confirmatory cells", "P2", "abstract", "(72 of 72 cells)", "(72 of 72 cells)" if "$72/72$" in p2 else "MISSING"),
    ]
    return E


def main() -> int:
    F = frozen.load()
    text = (PAPER / "research3.tex").read_text(encoding="utf-8") + "".join(p.read_text(encoding="utf-8") for p in sorted((PAPER / "tables").glob("*.tex")))
    E = entries(F); bad = []
    import re
    tex = (PAPER / "research3.tex").read_text(encoding="utf-8"); body_tex, appendix = tex.split("\\appendix", 1)
    body = body_tex + "".join(p.read_text(encoding="utf-8") for p in sorted((PAPER / "tables").glob("*.tex"))) + (PAPER / "figures/fig1_chain.tex").read_text(encoding="utf-8") + (PAPER / "research3.bib").read_text(encoding="utf-8")
    LEAK = re.compile(r"\bR[23]-[0-9A-Z]|R[23]_|\bR-\d|Research~?\s?(2|3|II|III)\b|Paper~?\s?(II|III)\b|r2-11|R[23]-final|\bR[123]/R[123]\b")
    for m in LEAK.finditer(body):
        ln = body[:m.start()].count("\n") + 1; bad.append(f"internal ID outside provenance: '{m.group(0)}' (concatenated reader-facing text, line {ln})")
    for nm, rid, pat in MAPPING:
        if nm not in appendix or rid.replace(" ", "~") not in appendix and rid not in appendix: bad.append(f"mapping {nm} -> {rid} missing in appendix")
        if not glob.glob(str(REPO / pat)): bad.append(f"mapping directory {pat} for {rid} not found")
    for nm, rid, pat in MAPPING:
        if nm.split(" (")[0] not in body and not nm.startswith(("Present-study", "Prospective Predictive")): bad.append(f"reader-facing name '{nm}' unused in the paper body")
    for name, src, field, printed, computed in E:
        if printed != computed: bad.append(f"{name}: printed '{printed}' != recomputed '{computed}'")
        elif printed not in text: bad.append(f"{name}: '{printed}' not found in the manuscript")
    # every classification label printed must be a frozen one
    labels = ["R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_PASS", "R3_03B_EXPLAINED", "R3_04_CLOSED_NET_LOSS", "R3_05_R1_FIDELITY_PASS", "R3_05_K1_HEADROOM_PASS", "R3_05_K2_RETIRE_CURRENT_PP",
              "R3_05_PREDICTIVE_RETENTION_KILLED", "R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME"]
    closure = json.loads((REPO / "experiments/R3-final-closure/EVIDENCE_CHAIN.json").read_text())
    frozen_labels = set(s["classification"] for s in closure["stages"] if s["classification"]) | {closure["r3_04_closure"]["status"], json.loads((REPO / "experiments/R3-final-closure/FINAL_RESULT.json").read_text())["classification"]}
    for l in labels:
        if l.replace("_", "\\_") not in text: bad.append(f"label {l} not printed")
        if l not in frozen_labels: bad.append(f"label {l} is not a frozen classification")
    # every freeze hash and commit in the provenance appendix must match the closure chain
    for s in closure["stages"]:
        h = s["freeze_sha256"] or s["freeze_file_sha256"]
        if h not in text or s["commit"][:7] not in text: bad.append(f"provenance for {s['stage']} missing or wrong")
    for h in ("760a22d69abdd207c7ecd4b4f1012b85711b20aa3cb623cddb55d0c8802a5717", "3aa389e", closure["r3_04_closure"]["file_sha256"], "91aa9b4", freeze_id("experiments/R3-03B-microbench-evidence"), commit_of("experiments/R3-03B-microbench-evidence/FREEZE.json")):
        if h not in text: bad.append(f"provenance hash/commit {h} missing")
    # citations: every \cite key exists in research3.bib
    import re
    bib = (PAPER / "research3.bib").read_text(encoding="utf-8")
    for k in set(x.strip() for m in re.findall(r"\\cite\{([^}]*)\}", text) for x in m.split(",")):
        if "{" + k + "," not in bib: bad.append(f"citation {k} not in research3.bib")
    rows = ["| # | Claim | Printed | Stage | Artifact | Field | Freeze | Commit |", "|---|---|---|---|---|---|---|---|"]
    for i, (name, src, field, printed, computed) in enumerate(E, 1):
        for s in src.split("/"):
            st, d, art = SRC[s]
            rows.append(f"| {i} | {name} | `{printed}` | {st} | `{art}` | `{field}` | `{freeze_id(d)}` | `{commit_of(d + '/FREEZE.json') if (REPO / d / 'FREEZE.json').exists() else commit_of(art.split(' ')[0])}` |")
    tbl = ["", "## Generated tables and figures", "", "| Asset | Generated by | Source |", "|---|---|---|",
           "| Table 3 `tables/table2_runtime.tex` | `scripts/build_assets.py` | R3-04 CLOSURE.json `primary_1.5GiB`, `control_8GiB_descriptive` |",
           "| Table 4 `tables/table3_mechanism.tex` | `scripts/build_assets.py` | R2 RESULTS.json (A, Belady), R3 CES_RESULTS.json (CES, PP), R4 EVAL_RESULTS.json (NUE_LO, NUE_X), Phase 0 kappa_low |",
           "| Figure 2 (`figures/fig3_runtime.pdf`) | `scripts/build_assets.py` | R3-04 CLOSURE.json |",
           "| Figure 3 (`figures/fig2_headroom_capture.pdf`) | `scripts/build_assets.py` | as Table 3 |",
           "| Figure 1 (`figures/fig1_chain.tex`) | hand-written TikZ | reader-facing stage names and gates (Table 1); numbers as in the stage tables |",
           "| Tables 1, 2, 5 and the appendix tables | in `research3.tex` | labels and hashes checked by `scripts/verify_paper.py` against `experiments/R3-final-closure/` |"]
    mp = ["", "## Reader-facing name to repository identifier", "",
          "Canonical repository identifiers are unchanged; the paper body uses only the reader-facing names (internal numbering: preceding study = Research 2, present study = Research 3).", "",
          "| Reader-facing name | Repository ID | Artifact directory | Commit | Freeze |", "|---|---|---|---|---|"]
    for nm, rid, pat in MAPPING:
        for d in sorted(glob.glob(str(REPO / pat))):
            rel = str(Path(d).relative_to(REPO)).replace("\\", "/")
            cm = commit_of(rel + "/FREEZE.json") if (Path(d) / "FREEZE.json").exists() else commit_of(rel)
            mp.append(f"| {nm} | {rid} | `{rel}` | `{cm}` | `{freeze_id(rel)}` |")
    tbl = tbl + mp
    (PAPER / "SOURCE_MAP.md").write_text("# Source map — Research 3 paper\n\nGenerated by `scripts/verify_paper.py`. Every entry is recomputed from the frozen artifact and must appear verbatim in `research3.tex` or `tables/`. "
                                         "Freeze = the `freeze_sha256` of the evidence directory's FREEZE.json (or the FREEZE.json file hash where the directory has no self-hash); Commit = the commit that last changed that FREEZE.json.\n\n" + "\n".join(rows + tbl) + "\n", encoding="utf-8")
    print(f"{len(E)} numerical claims, {len(labels)} labels, {len(closure['stages'])} provenance rows, {len(MAPPING)} name-to-ID mappings, leak scan checked")
    print("VERIFY", "OK" if not bad else "FAIL"); [print(" -", b) for b in bad]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
