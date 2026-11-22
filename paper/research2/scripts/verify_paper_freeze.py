#!/usr/bin/env python3
"""Consistency checks for the Research II-A + II-B paper freeze and
Oracle-semantics scientific amendment.

Run from paper/research2/ (or any cwd; resolves paths from this file).

Checks:
  II-A (preserved): headline numbers in II-A-FREEZE.json match frozen II-A
  data; run IDs/trace SHA consistent across II-A freeze, data README, claim
  ledger. Historical freeze records are not rewritten by the amendment.
  II-B: headline numbers and canonical run IDs/digests in II-B-FREEZE.json
  match frozen II-B data/manifests/summaries; R2-08 capture-ID audit is
  explicitly represented; claim ledger contains required II-B claim IDs.
  Amendment: ORACLE-SEMANTICS-AMENDMENT.json present with required fields
  (AUDIT-ORACLE-001, audit commit, historical freezes preserved, numerical
  evidence unchanged).
  Manuscript hygiene: forbidden performance-claim phrases absent under
  sections/; no numeric percentage attached to "causally recovered margin"
  or a populated recovery metric; no active "Oracle-Bounded" framing in
  manuscript/sections.

Exits non-zero on any failure, printing every problem found.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def load_json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


# --- II-A (preserved checks) ------------------------------------------------
freeze_a = load_json("II-A-FREEZE.json")

r2_02_comparison = load_json("data/r2_02/comparison.json")
actual_458 = r2_02_comparison["oracle_gaps_by_baseline"]["Global-LRU"]["relative_miss_byte_reduction"]
claimed_458 = freeze_a["canonical_headline_metrics"]["r2_02_at_4gib"]["relative_miss_byte_reduction_oracle_vs_global_lru"]
check(abs(actual_458 - claimed_458) < 1e-9,
      f"R2-02 4GiB relative reduction mismatch: data={actual_458} freeze={claimed_458}")

r2_04_margin = load_json("data/r2_04/recoverable_margin.json")
budget_gib_map = {1610612736: "1.5GiB", 2147483648: "2GiB", 4294967296: "4GiB",
                  8589934592: "8GiB", 17179869184: "16GiB"}
sweep = freeze_a["canonical_headline_metrics"]["r2_03_r2_04_budget_sweep_relative_headroom"]
abs_sweep = freeze_a["canonical_headline_metrics"]["r2_04_budget_sweep_absolute_headroom_bytes"]
for entry in r2_04_margin.get("policy_information_headroom", []):
    bbytes = entry.get("budget_bytes")
    label = budget_gib_map.get(bbytes)
    if label is None:
        continue
    rel = entry.get("relative_miss_byte_reduction")
    abs_bytes = entry.get("absolute_miss_byte_reduction")
    if rel is not None:
        check(abs(round(rel, 4) - sweep[label]) < 5e-4,
              f"{label} relative headroom mismatch: data={rel} freeze={sweep[label]}")
    if abs_bytes is not None:
        check(abs_bytes == abs_sweep[label],
              f"{label} absolute headroom mismatch: data={abs_bytes} freeze={abs_sweep[label]}")

run_ids_a = {
    "R2-01": "bb6d8ca34dbc4537a43d54be30c9f3e1",
    "R2-02": "238c8ee7f0914df7b28942ec99627c24",
    "R2-03": "d957077073374f1e9fd1e101a002946f",
    "R2-04": "cd2b58b019314fafb90304234e6d1907",
}
trace_sha = "e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1"
for exp, rid in run_ids_a.items():
    check(freeze_a["canonical_runs"][exp]["run_id"] == rid, f"{exp} run_id mismatch in II-A-FREEZE.json")
check(freeze_a["source_trace_sha256"] == trace_sha, "trace SHA mismatch in II-A-FREEZE.json")

data_readme = (ROOT / "data/README.md").read_text(encoding="utf-8")
claim_ledger = (ROOT / "claims/claim_ledger.md").read_text(encoding="utf-8")
for exp, rid in run_ids_a.items():
    check(rid in data_readme, f"{exp} run_id {rid} missing from data/README.md")
    check(rid in claim_ledger, f"{exp} run_id {rid} missing from claims/claim_ledger.md")
check(trace_sha in data_readme, "trace SHA missing from data/README.md")
check(trace_sha in claim_ledger, "trace SHA missing from claims/claim_ledger.md")

# --- II-B -------------------------------------------------------------------
freeze_b = load_json("II-B-FREEZE.json")
check(freeze_b["research_phase_classification"].get("II-B") == "CLOSED / PASS / PAPER-FROZEN",
      "II-B phase classification mismatch in II-B-FREEZE.json")
check(freeze_b["research_phase_classification"].get("II-A") == "CLOSED / PASS / PAPER-FROZEN",
      "II-A phase classification mismatch in II-B-FREEZE.json")
check(freeze_b["research_phase_classification"].get("II-C") == "FUTURE / NOT STARTED",
      "II-C phase classification mismatch in II-B-FREEZE.json")

iib_runs = {
    "R2-05": ("da82e15b61c94019840477642618565a", "4a1b72cdfcd37144446fc1a106b6fa8d559d10bc0729e1baf16dda7c1fa620df"),
    "R2-06": ("2e075161f02443ff822279add6938ca4", "a727edf42735286420d326baad481687167d3d0aba0444a527183c084140926e"),
    "R2-07": ("af28e8c6dc5044a18e4a86137d59156a", "ad36e615970b196b64ba3a3fdc2559e252cc8627e5d17d0d830d16742f59281a"),
    "R2-08": ("6165471a12574e05b48932aac2f1626c", "f5db4e0bf43ce4243edb303441e82ceefa5a0aeb5754143292f1230f220527c3"),
}
for exp, (rid, dig) in iib_runs.items():
    fr = freeze_b["canonical_runs"][exp]
    check(fr["run_id"] == rid, f"{exp} run_id mismatch in II-B-FREEZE.json")
    check(fr["canonical_digest"] == dig, f"{exp} digest mismatch in II-B-FREEZE.json")
    data_dir = f"r2_{exp.split('-')[1]}" if "-" in exp else exp.lower()
    summ = load_json(f"data/{data_dir}/summary.json")
    check(summ["run_id"] == rid, f"{exp} summary run_id mismatch")
    check(summ.get("canonical_digest") == dig, f"{exp} summary digest mismatch")
    check(rid in data_readme, f"{exp} run_id missing from data/README.md")
    check(rid in claim_ledger, f"{exp} run_id missing from claims/claim_ledger.md")
    check(dig in claim_ledger, f"{exp} digest missing from claims/claim_ledger.md")

# Key II-B numbers vs frozen data
s5 = load_json("data/r2_05/summary.json")["headline"]
s6 = load_json("data/r2_06/summary.json")["headline"]
s7 = load_json("data/r2_07/summary.json")["headline"]
s8 = load_json("data/r2_08/summary.json")
h5 = freeze_b["canonical_headline_metrics"]["r2_05"]
h6 = freeze_b["canonical_headline_metrics"]["r2_06"]
h7 = freeze_b["canonical_headline_metrics"]["r2_07"]
h8 = freeze_b["canonical_headline_metrics"]["r2_08"]

def near(a, b, tol=1e-12):
    return abs(float(a) - float(b)) <= tol

check(s5["total_routing_requests"] == h5["total_routing_requests"], "R2-05 total_routing_requests mismatch")
check(near(s5["median_normalized_entropy"], h5["median_normalized_entropy"]), "R2-05 median_normalized_entropy mismatch")
check(near(s5["median_effective_expert_fraction"], h5["median_effective_expert_fraction"]), "R2-05 median_effective_expert_fraction mismatch")
check(near(s5["median_top_8_share"], h5["median_top_8_share"]), "R2-05 median_top_8_share mismatch")
check(s5["experts_for_80pct_median"] == h5["median_experts_for_80pct_traffic"], "R2-05 experts_for_80pct mismatch")

check(s6["total_transition_observations"] == h6["total_pairwise_transition_observations"], "R2-06 observations mismatch")
check(near(s6["median_nmi"], h6["median_nmi"]), "R2-06 median_nmi mismatch")
check(near(s6["fraction_layers_with_nmi_positive"], h6["fraction_layers_positive_nmi"]), "R2-06 positive NMI fraction mismatch")
check(near(s6["median_conditional_entropy"], h6["median_conditional_entropy"]), "R2-06 conditional entropy mismatch")

check(s7["total_reuse_events"] == h7["total_reuse_events"], "R2-07 reuse events mismatch")
check(near(s7["median_immediate_reuse_fraction"], h7["median_immediate_reuse"]), "R2-07 immediate reuse mismatch")
check(near(s7["observed_vs_null"]["immediate_reuse_fraction"]["median_null"], h7["observed_vs_null"]["immediate_reuse"]["null"]), "R2-07 null immediate reuse mismatch")
check(near(s7["median_working_set_size_by_window"]["8"], h7["median_working_set"]["W8"]), "R2-07 W8 mismatch")
check(near(s7["median_working_set_size_by_window"]["32"], h7["median_working_set"]["W32"]), "R2-07 W32 mismatch")

check(s8["headline"]["matched_decode_horizon"] == h8["matched_decode_horizon_H"], "R2-08 H mismatch")
check(near(s8["headline"]["median_temporal_jsd"], h8["median_temporal_jsd"]), "R2-08 median temporal JSD mismatch")
check(near(s8["headline"]["median_same_workload_replicate_jsd"], h8["median_same_workload_replicate_jsd"]), "R2-08 replicate JSD mismatch")
check(near(s8["headline"]["median_cross_workload_jsd"], h8["median_cross_workload_jsd"]), "R2-08 cross-workload JSD mismatch")
check(near(s8["headline"]["cross_workload_to_temporal_ratio"], h8["cross_workload_to_temporal_ratio"]), "R2-08 ratio mismatch")
check(s8["comparison"]["regime"] == h8["predeclared_regime"], "R2-08 regime mismatch")
check(s8["headline"]["all_configuration_fingerprints_compatible"] is True, "R2-08 configuration compatibility not true")

# R2-08 capture-ID audit documented (section 11)
cm = load_json("data/r2_08/capture_matrix.json")
reused = cm["cells"].get("coding-v1/seed-42", {})
check(reused.get("capture_run_id") == "bb6d8ca34dbc4537a43d54be30c9f3e1", "R2-08 reused capture_run_id unexpected")
new_nulls = 0
for cid, cell in cm["cells"].items():
    if cid == "coding-v1/seed-42":
        continue
    if cell.get("capture_run_id") is None and cell.get("capture_source") is None:
        new_nulls += 1
        check(bool(cell.get("trace_sha256")), f"R2-08 {cid} has null capture IDs but missing trace SHA")
check(new_nulls == 7, f"Expected 7 new R2-08 cells with null capture IDs, found {new_nulls}")
audit = freeze_b.get("r2_08_capture_id_audit", {})
check(audit.get("status") == "DOCUMENTED_INTENTIONAL_REPRESENTATION", "R2-08 capture-ID audit not documented in II-B-FREEZE.json")
check("capture_run_id" in claim_ledger and "6165471a12574e05b48932aac2f1626c" in claim_ledger,
      "R2-08 capture-ID audit not documented in claim ledger")
prov_app = (ROOT / "sections/12_provenance_appendix.tex").read_text(encoding="utf-8")
check("capture" in prov_app.lower() and "null" in prov_app.lower() and "6165471a12574e05b48932aac2f1626c" in prov_app,
      "R2-08 capture-ID audit not documented in provenance appendix")

# Claim ledger B-C IDs
for cid in ["B-C1", "B-C2", "B-C3", "B-C4", "B-C5", "B-C6", "B-C7", "B-C8", "B-C9", "B-C10"]:
    check(cid in claim_ledger, f"claim ID {cid} missing from claim ledger")

# --- Oracle-semantics amendment ---------------------------------------------
amend = load_json("ORACLE-SEMANTICS-AMENDMENT.json")
check(amend.get("audit_id") == "AUDIT-ORACLE-001", "amendment audit_id mismatch")
check(amend.get("audit_commit") == "3789a8511e9623f953dda5a610c9d7897e4ae63b",
      "amendment audit_commit mismatch")
check(amend.get("historical_freezes_rewritten") is False,
      "amendment must not rewrite historical freezes")
check(amend.get("numerical_experiment_evidence_unchanged") is True,
      "amendment must state numerical evidence unchanged")
for rec in ("paper/research2/II-A-FREEZE.json", "paper/research2/II-B-FREEZE.json"):
    check(rec in (amend.get("historical_freeze_records_preserved") or []),
          f"amendment missing preserved record {rec}")
check("AUDIT-ORACLE-001" in claim_ledger, "AUDIT-ORACLE-001 missing from claim ledger")
check("ORACLE-SEMANTICS-AMENDMENT" in claim_ledger, "ORACLE-SEMANTICS-AMENDMENT missing from claim ledger")
check(amend.get("amendment_commit_message") == "Amend Research II-A and II-B Oracle semantics",
      "amendment commit message mismatch")

# --- Manuscript hygiene -----------------------------------------------------
forbidden_phrases = [
    "performance improvement",
    "memory saving",
    "faster inference",
    "demonstrated recoverability",
]
sections_dir = ROOT / "sections"
for path in sorted(sections_dir.glob("*.tex")):
    text = path.read_text(encoding="utf-8").lower()
    for phrase in forbidden_phrases:
        check(phrase not in text, f"forbidden phrase '{phrase}' found in {path.relative_to(ROOT)}")

causal_pattern = re.compile(r"causally recovered margin[^.]{0,80}?\d+(\.\d+)?\s*%", re.IGNORECASE)
for path in sorted(sections_dir.glob("*.tex")):
    text = path.read_text(encoding="utf-8")
    m = causal_pattern.search(text)
    check(m is None, f"numeric percentage attached to 'causally recovered margin' in {path.relative_to(ROOT)}: {getattr(m, 'group', lambda *_: '')(0)!r}")

# No populated recovery metric (comparator-relative or historical name).
for path in [ROOT / "manuscript.tex", *sorted(sections_dir.glob("*.tex"))]:
    text = path.read_text(encoding="utf-8")
    for name in (r"FractionOfOracleHeadroomRecovered", r"RecoveryOfBeladyGap"):
        if re.search(name + r"\s*[=:]\s*[-+]?\d", text):
            check(False, f"{name} appears numerically populated in {path.relative_to(ROOT)}")

# No active post-audit Oracle-bound/headroom/exploit framing in manuscript/sections.
# Historical policy name "Oracle-Global-Belady" and qualified historical provenance
# outside manuscript/sections are not banned. "not a proven upper bound" is valid.
active_banned = [
    ("Oracle-Bounded", "active 'Oracle-Bounded' framing"),
    ("recovers Oracle headroom", "active 'recovers Oracle headroom' phrasing"),
    ("Oracle headroom", "active 'Oracle headroom' phrasing"),
    ("timing information that the Oracle exploits", "active 'Oracle exploits' phrasing"),
    ("The fraction in Eq.", "active 'fraction in Eq.' recovery phrasing"),
    ("same declared policy set", "ambiguous R2-03 'same declared policy set' phrasing"),
    ("LFU outside 4 GiB is unmeasured", "stale claim that canonical R2-03 LFU sweep is unmeasured"),
    ("This comparison does not generalize LFU ordering to other budgets", "stale claim contradicting canonical auxiliary R2-03 LFU sweep"),
]
for path in [ROOT / "manuscript.tex", *sorted(sections_dir.glob("*.tex"))]:
    text = path.read_text(encoding="utf-8")
    for phrase, label in active_banned:
        check(phrase not in text, f"{label} found in {path.relative_to(ROOT)}")

# --- Report -----------------------------------------------------------------
if errors:
    print(f"FAIL: {len(errors)} consistency problem(s) found:\n")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("PASS: all II-A and II-B paper-freeze consistency checks passed.")
sys.exit(0)
