# Argine Research Part 2 — Paper Tree (II-A + II-B cumulative freeze)

This directory holds the cumulative publication-oriented manuscript for
Argine Research Part 2, covering the completed and closed **Research
II-A** programme (R2-01 through R2-04) and the completed and closed
**Research II-B** programme (R2-05 through R2-08). Research II-C has not
started; the manuscript includes an II-C placeholder section and makes no
II-C claims.

This is a separate, sibling tree to the pre-existing `paper/` directory
(the finished Part 1 manuscript, "Argine: Expert Streaming Under Hard
Memory Constraints"). Nothing in `paper/` was modified by these freezes.

## Layout

```
paper/research2/
├── manuscript.tex            # main LaTeX file; \input's sections/ in order
├── sections/                  # numbered paper sections (II-A, II-B, II-C placeholder)
├── tables/
│   ├── generate_tables.py     # reads data/, emits II-A and II-B table fragments
│   └── table*.tex             # generated LaTeX table fragments
├── figures/
│   ├── make_figures.py        # reads frozen data/, emits II-A and II-B figures
│   └── figure*.{pdf,png}      # generated vector (pdf) + preview (png) figures
├── data/                      # frozen snapshots of canonical evidence files cited
│   ├── r2_01..r2_04/          # II-A evidence snapshot
│   └── r2_05..r2_08/          # II-B evidence snapshot
├── claims/
│   └── claim_ledger.md        # every numerical/scientific claim with source IDs/digests
├── references.bib             # preserved vetted citations; no speculative II-B/II-C additions
├── II-A-FREEZE.json           # historical II-A freeze manifest (preserved)
├── II-B-FREEZE.json           # II-B freeze manifest
└── README.md                  # this file
```

## Rebuilding tables and figures

Regenerates table fragments and figures from the frozen `data/` snapshot:

```
python tables/generate_tables.py
python figures/make_figures.py
```

## Building the PDF

```
cd paper/research2
latexmk -pdf -interaction=nonstopmode manuscript.tex
```

On this Windows environment, a direct `pdflatex`/`bibtex`/`pdflatex`/
`pdflatex` sequence can be used if `latexmk` is unavailable.

**Known build-environment limitation:** the Part 1 paper uses
`newtxtext`/`newtxmath`, which require `texlive-fonts-extra`. This
manuscript uses `amsmath`+`amssymb` with default Latin Modern fonts and
`\usepackage[expansion=false]{microtype}` as a build-environment
substitution; swapping back to NewTX on a machine with the required fonts
needs no other source change.

## Validation

`scripts/verify_paper_freeze.py` checks II-A and II-B freeze consistency:
headline numbers match frozen data, canonical run IDs/digests are
consistent across freeze manifests/data/README/claim ledger, the R2-08
capture-ID audit is documented, forbidden performance phrases are absent
under `sections/`, and no numeric causal-recovery claim is present.

Run:

```
python scripts/verify_paper_freeze.py
```

## Status

- Research II-A: **CLOSED / PASS / PAPER-FROZEN** (`II-A-FREEZE.json` preserved; scientific Oracle-semantics amendment applied, see `ORACLE-SEMANTICS-AMENDMENT.json`).
- Research II-B: **CLOSED / PASS / PAPER-FROZEN** (`II-B-FREEZE.json`; scientific Oracle-semantics amendment applied, see `ORACLE-SEMANTICS-AMENDMENT.json`).
- `AUDIT-ORACLE-001`: **CLOSED / CASE C / resolved by paper amendment**.
- Research II-C: **FUTURE / NOT STARTED** (placeholder only).

The R2-08 capture-ID audit found seven R2-08-managed new capture cells
with `capture_run_id`/`capture_source` null while preserving trace SHA,
path, workload/seed/config/model/runtime identity, validation state, and
canonical R2-08 run binding. This is documented as an intentional
R2-08-internal provenance representation; no capture IDs were invented and
no scientific traces were modified.

Next action: **R2-09B prerequisite/evaluation-design work** (including a
held-out EVAL contract) before the causal baseline tournament or predictor
evaluation. R2-09 remains **not started**.
