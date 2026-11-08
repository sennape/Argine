#!/usr/bin/env python3
"""Lightweight consistency check for the Research II-A paper freeze.

Run from paper/research2/. Checks:
  1. Headline numbers in II-A-FREEZE.json match the frozen data/ snapshot.
  2. Run IDs and trace SHA are consistent across II-A-FREEZE.json,
     data/README.md, and claims/claim_ledger.md.
  3. No forbidden performance-claim phrase appears under sections/.
  4. No numeric percentage is attached to "causally recovered margin"
     anywhere under sections/.

Exits non-zero on any failure, printing every problem found (does not
stop at the first one).
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
errors = []


def check(condition, message):
    if not condition:
        errors.append(message)


# --- 1. Headline numbers vs frozen data snapshot -----------------------
freeze = json.loads((ROOT / "II-A-FREEZE.json").read_text())

r2_02_comparison = json.loads((ROOT / "data/r2_02/comparison.json").read_text())
actual_458 = r2_02_comparison["oracle_gaps_by_baseline"]["Global-LRU"]["relative_miss_byte_reduction"]
claimed_458 = freeze["canonical_headline_metrics"]["r2_02_at_4gib"]["relative_miss_byte_reduction_oracle_vs_global_lru"]
check(abs(actual_458 - claimed_458) < 1e-9,
      f"R2-02 4GiB relative reduction mismatch: data={actual_458} freeze={claimed_458}")

r2_04_margin = json.loads((ROOT / "data/r2_04/recoverable_margin.json").read_text())
budget_gib_map = {1610612736: "1.5GiB", 2147483648: "2GiB", 4294967296: "4GiB",
                   8589934592: "8GiB", 17179869184: "16GiB"}
sweep = freeze["canonical_headline_metrics"]["r2_03_r2_04_budget_sweep_relative_headroom"]
abs_sweep = freeze["canonical_headline_metrics"]["r2_04_budget_sweep_absolute_headroom_bytes"]
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

# --- 2. Run ID / trace SHA consistency ----------------------------------
run_ids = {
    "R2-01": "bb6d8ca34dbc4537a43d54be30c9f3e1",
    "R2-02": "238c8ee7f0914df7b28942ec99627c24",
    "R2-03": "d957077073374f1e9fd1e101a002946f",
    "R2-04": "cd2b58b019314fafb90304234e6d1907",
}
trace_sha = "e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1"

for exp, rid in run_ids.items():
    check(freeze["canonical_runs"][exp]["run_id"] == rid,
          f"{exp} run_id mismatch in II-A-FREEZE.json")
check(freeze["source_trace_sha256"] == trace_sha, "trace SHA mismatch in II-A-FREEZE.json")

data_readme = (ROOT / "data/README.md").read_text()
for exp, rid in run_ids.items():
    check(rid in data_readme, f"{exp} run_id {rid} missing from data/README.md")
check(trace_sha in data_readme, "trace SHA missing from data/README.md")

claim_ledger = (ROOT / "claims/claim_ledger.md").read_text()
check(trace_sha in claim_ledger, "trace SHA missing from claims/claim_ledger.md")
for exp, rid in run_ids.items():
    check(rid in claim_ledger, f"{exp} run_id {rid} missing from claims/claim_ledger.md")

# --- 3. Forbidden performance-claim phrases -----------------------------
forbidden_phrases = [
    "performance improvement",
    "memory saving",
    "faster inference",
    "demonstrated recoverability",
]
sections_dir = ROOT / "sections"
for path in sorted(sections_dir.glob("*.tex")):
    text = path.read_text().lower()
    for phrase in forbidden_phrases:
        if phrase in text:
            errors.append(f"forbidden phrase '{phrase}' found in {path.relative_to(ROOT)}")

# --- 4. No numeric percentage on "causally recovered margin" -----------
causal_pattern = re.compile(r"causally recovered margin[^.]{0,80}?\d+(\.\d+)?\s*%", re.IGNORECASE)
for path in sorted(sections_dir.glob("*.tex")):
    text = path.read_text()
    m = causal_pattern.search(text)
    if m:
        errors.append(
            f"numeric percentage attached to 'causally recovered margin' in "
            f"{path.relative_to(ROOT)}: {m.group(0)!r}"
        )

# --- Report --------------------------------------------------------------
if errors:
    print(f"FAIL: {len(errors)} consistency problem(s) found:\n")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("PASS: all II-A paper-freeze consistency checks passed.")
    sys.exit(0)
