# Argine Research Part 2 — Paper Tree (Research II-A tranche)

This directory holds the first publication-ready tranche of the Argine
Research Part 2 paper, covering only the completed and closed **Research
II-A** programme (R2-01 through R2-04). Research II-B and II-C have not
started; the manuscript includes placeholder sections for them and makes
no II-B/II-C claims.

This is a separate, sibling tree to the pre-existing `paper/` directory
(the finished Part 1 manuscript, "Argine: Expert Streaming Under Hard
Memory Constraints"). Nothing in `paper/` was modified by this freeze.

## Layout

```
paper/research2/
├── manuscript.tex          # main LaTeX file; \input's sections/ in order
├── sections/                # one .tex file per numbered paper section
├── tables/
│   ├── generate_tables.py   # reads data/, emits table*_*.tex fragments
│   └── table*_*.tex          # generated LaTeX table fragments (checked in
│                              # for reproducibility inspection, but can be
│                              # regenerated from data/ at any time)
├── figures/
│   ├── make_figures.py      # reads data/r2_03/comparison.json, emits figures
│   └── figure*_*.{pdf,png}   # generated vector (pdf) + preview (png) figures
├── data/                    # frozen byte-for-byte snapshot of the specific
│                              canonical evidence files the paper cites —
│                              see data/README.md. NOT the source of truth.
├── claims/
│   └── claim_ledger.md      # every numerical/scientific claim in the paper,
│                              with its source run ID, artifact, and metric key
├── references.bib           # bibliography (entries preserved verbatim from
│                              the Part 1 paper's vetted reference list, plus
│                              a clearly marked II-B/II-C scaffold)
├── II-A-FREEZE.json         # freeze manifest: run IDs, trace SHA, git commits,
│                              headline metrics, and content digests, so this
│                              tranche is auditable later
└── README.md                 # this file
```

## Rebuilding the paper

From canonical evidence (regenerates the `data/` snapshot, tables, and
figures; only meaningful on a machine with access to the Argine3 evidence
root):

```
python3 tables/generate_tables.py
python3 figures/make_figures.py
```

Building the PDF:

```
cd paper/research2
latexmk -pdf -interaction=nonstopmode manuscript.tex
```

**Known build-environment limitation:** the Part 1 paper's `main.tex` uses
`newtxtext`/`newtxmath` (Times-like text/math fonts), which require the
`texlive-fonts-extra` package. That package is not installed in this
session's build environment and could not be installed (no `sudo`/`tlmgr`
write access). `manuscript.tex` therefore uses `amsmath`+`amssymb` with the
default Latin Modern fonts instead, and disables microtype's font-expansion
feature (`\usepackage[expansion=false]{microtype}`, since expansion requires
scalable Type 1 fonts not available here). This is a build-environment
substitution, not a deliberate style departure from Part 1; swapping back to
`newtxtext`/`newtxmath` on a machine with `texlive-fonts-extra` installed
requires no other source change.

## Validation

`scripts/verify_paper_freeze.py` (run from this directory) checks that:
- every headline numeric value quoted in `II-A-FREEZE.json` and in the
  generated table fragments matches the frozen `data/` snapshot;
- the four canonical run IDs and the source trace SHA-256 appear
  consistently across `II-A-FREEZE.json`, `data/README.md`, and
  `claims/claim_ledger.md`;
- none of the forbidden performance-claim phrases
  ("performance improvement", "memory saving", "faster inference",
  "demonstrated recoverability") appear anywhere under `sections/`;
- no numeric percentage is attached to "causally recovered margin"
  anywhere under `sections/` (causal recovery must always read
  "NOT YET MEASURED -- II-C").

Run it with:

```
python3 scripts/verify_paper_freeze.py
```

## Status

Research II-A: **CLOSED / PASS / PAPER-FROZEN**. See `II-A-FREEZE.json`
for the full freeze manifest and `claims/claim_ledger.md` for the frozen
claim set. Research II-B is ready to start (not yet started); Research
II-C depends on II-B and has not started.
