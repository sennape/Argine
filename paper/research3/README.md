# Research 3 paper

`research3.tex` — "Predictive Structure Is Not Retention Value: A Prospective Falsification of Prediction-Led Expert Retention in Memory-Constrained Mixture-of-Experts Inference". Written from the frozen Research 3 evidence (closure freeze `760a22d69abdd207c7ecd4b4f1012b85711b20aa3cb623cddb55d0c8802a5717`, commit `3aa389e`). No new research.

| File | Content |
|---|---|
| `research3.tex` / `research3.pdf` | manuscript (14 pages) |
| `research3.bib` | 14 external entries inherited from the preceding study's bibliography (ExpertFlow, LayerScope, MoE-Infinity and ProMoE corrected against the current arXiv records on 2026-11-22) + 2 internal repository entries |
| `tables/` | Table 3 (runtime) and Table 4 (mechanism), generated; Tables 1, 2, 5 and the appendix tables are in the manuscript |
| `figures/` | Figure 1 `fig1_chain.tex` (TikZ); Figure 2 `fig3_runtime.pdf`; Figure 3 `fig2_headroom_capture.pdf` (file names predate the final figure order) |
| `scripts/frozen.py` | read-only loader of the frozen artifacts |
| `scripts/build_assets.py` | regenerates tables and figures from the frozen artifacts |
| `scripts/verify_paper.py` | recomputes 64 numerical claims, checks labels, provenance hashes/commits, citation keys, the 22 reader-facing-name -> repository-ID mappings, and scans the paper body (outside the appendix) for internal IDs; writes `SOURCE_MAP.md` |
| `SOURCE_MAP.md` | claim -> stage, artifact, field, freeze, commit; plus the reader-facing name -> repository ID -> directory -> commit -> freeze table |
| `CLAIM_AUDIT.md` | claim classification and flagged sentences |

Reproduce (repository root):

    python paper/research3/scripts/build_assets.py
    python paper/research3/scripts/verify_paper.py
    cd paper/research3 && latexmk -pdf research3.tex

Fonts: uses newtx (as the Research 2 manuscript) when installed, otherwise falls back to mathptmx; the committed PDF was built with the fallback (TeX Live without newtx).

Related work: no new external citation was added. Entries inherited from Paper II were vetted for Papers I/II; they are characterized here at title level only. Literature that would strengthen §8.2/§10 but needs external verification before citation: classical replacement policies used as baselines in Research 2 (LRU-K, 2Q), adaptive replacement (ARC, LIRS), and oracle-imitating learned replacement (e.g. Hawkeye, Glider).

## Nomenclature

The paper body uses descriptive scientific names only (for example *Runtime Semantic Equivalence*, *Current-Event Shielding*, *Causal Next-Use Eviction*; the preceding study is described in thirteen reader-facing stages). The repository's internal identifiers (R2-*, R3-*, `R3_*` classification labels) are unchanged in the repository and appear in the paper only in the provenance appendix (Appendix A, Tables 6-8) and in `SOURCE_MAP.md` / `CLAIM_AUDIT.md`. `verify_paper.py` fails if an internal ID appears outside the appendix.
