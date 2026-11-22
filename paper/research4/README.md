# Research 4 paper

`research4.tex` — "Causal State Is Not Enough: Prospective Evidence That Tested Causal Eviction Policies Recover Too Little Clairvoyant Headroom in Expert Retention". Written exclusively from the frozen Research 4 evidence (closure state `R4_CFDC_CLOSED`, classification `R4_CFDC_CAUSAL_STATE_INSUFFICIENT`). No new research; no frozen artifact is modified. This directory is the canonical location of the paper; [`papers/research-4`](../../papers/research-4/README.md) only points here.

| File | Content |
|---|---|
| `research4.tex` / `research4.pdf` | manuscript (15 pages, A4) |
| `research4.bib` | 21 entries (13 inherited from the Research 3 bibliography, 8 added: 6 external, 2 internal repository records) |
| `tables/` | generated tables (results, miss bytes, held-out workloads, validation traces) |
| `figures/` | Figure 1 `fig1_design.tex` (TikZ); Figures 2 and 3 generated PDFs; `figure_data.json` (the plotted data) |
| `scripts/frozen.py` | read-only loader; recomputes every capture exactly from per-trace miss bytes and cross-checks the stored rationals |
| `scripts/build_assets.py` | regenerates tables, figure data and figures from the frozen artifacts |
| `scripts/claims.py` | the claims registry (value, meaning, artifact, field, calculation, displayed precision) |
| `scripts/verify_paper.py` | check-only verifier (no git); `--write` regenerates `SOURCE_MAP.md`; `--write --freeze` regenerates `PUBLICATION_FREEZE.json` |
| `SOURCE_MAP.md` | claim → artifact → field → calculation → precision (internal identifiers appear here and in the appendix only) |
| `PUBLICATION_FREEZE.json` | title, author, date, PDF/source-map/figure/table/script hashes, the three R4 freeze identities, verifier identity and result |
| `CLAIM_AUDIT.md` | what is established, made less plausible, unknown and not shown |

Reproduce (repository root):

    python paper/research4/scripts/build_assets.py
    python paper/research4/scripts/verify_paper.py
    cd paper/research4 && latexmk -pdf research4.tex

The committed PDF was built with `SOURCE_DATE_EPOCH=1795305600 FORCE_SOURCE_DATE=1 latexmk -pdf research4.tex` and the mathptmx font fallback (newtx is not installed in that TeX Live), as for the Research 3 paper. The verifier fails on any drift between the manuscript and the frozen evidence, on unregistered percentages or large integers in the prose, on internal identifiers or repository paths in the body, and on a PDF or source that no longer matches `PUBLICATION_FREEZE.json`.

Nomenclature: the body uses descriptive names only; the repository's internal identifiers (R2-*, R3-*, R4-*, `R4_CFDC_*`) appear only in Appendix A and in `SOURCE_MAP.md`.
