# Claim audit — Research 3 paper (`research3.tex`)

Categories: **DIRECTLY_MEASURED** (a frozen counter or timing), **DERIVED** (arithmetic on frozen values, recomputed by `scripts/verify_paper.py`),
**CAUSAL_WITHIN_PROTOCOL** (a causal contrast licensed by a pre-registered randomized/paired design or an exact counterfactual replay), **DESCRIPTIVE_ONLY**
(stated, not inferentially tested), **NOT_CLAIMED** (explicitly disclaimed). Every number is mapped in `SOURCE_MAP.md`.

| # | Claim (section) | Category | Basis | Note |
|---|---|---|---|---|
| 1 | Research 2 established causal predictive structure (recall@k 0.449817 vs 0.401473) (§2) | DIRECTLY_MEASURED (in Research 2) | Paper II abstract | Cited, not re-measured |
| 2 | Original runtime issued 144 policy calls per decode token vs 48 (§4.1) | DIRECTLY_MEASURED | R3-03A CONFORMANCE_RESULT, R3-03 audit | |
| 3 | Cause: one update per MUL_MAT_ID node (gate/up/down) (§4.1) | DIRECTLY_MEASURED | R3-03 audit README (source-level and trace-level) | |
| 4 | Effective per-event decay was the cube of the frozen value; aging accelerated; bookkeeping tripled (§4.1) | DERIVED | three identical updates per semantic event (audit) | Mechanistic consequence of the update rule, not separately measured |
| 5 | Corrected runtime conforms (24,672 events, 514 passes, digest, 0 violations, sham = b_selected) (§4.3) | DIRECTLY_MEASURED | R3-03A | Equivalence only; no performance claim |
| 6 | Microbench per-call costs 8.80 / 132.48 / 134.28 µs; predictor 123.68 µs; 5.94 ms/token (§5.1) | DIRECTLY_MEASURED / DERIVED | R3-03B microbench | Host-specific |
| 7 | "The dominant work is algorithmic: dense recomputation, hash probes, exponentiation, 128-way sort" (§5.1) | DESCRIPTIVE_ONLY | R3-05 Phase 0 COST_MAP (static code analysis) | Not a measured cost breakdown; **flagged** as interpretive |
| 8 | Bookkeeping explains the sham overhead (rho 0.9024, CI [0.8760, 0.9287]) (§5.2) | CAUSAL_WITHIN_PROTOCOL | R3-03B frozen Fieller rule, 20 ABBA blocks | At 8 GiB; host/runtime-specific |
| 9 | In-situ predictor cost 3.26x microbench (§5.2) | DERIVED | R3-03B FINAL_RESULT.performance | Descriptive ratio |
| 10 | R3-04 net loss: P-A +15.83 ms/token, CI [10.46, 21.19], -2.93% throughput (§6.2) | CAUSAL_WITHIN_PROTOCOL | R3-04 frozen decision rule, 18 paired blocks | Primary claim |
| 11 | Bookkeeping cost S-A +14.59 [9.87, 19.31]; retention effect P-S +1.24 [-2.84, 5.32] (§6.2) | CAUSAL_WITHIN_PROTOCOL | R3-04 estimands | P-S not distinguishable from 0 |
| 12 | 8 GiB control: bookkeeping cost, no detectable retention effect (§6.3) | DESCRIPTIVE_ONLY | 6 blocks, descriptive by protocol | "little to act on" is DERIVED from A miss bytes at 8 vs 1.5 GiB |
| 13 | Offline replay reproduces runtime counters integer-exactly (§7.1) | DIRECTLY_MEASURED | R3-05 R1 | |
| 14 | Oracle removes 40.673% of misses / 40.653% of bytes; ceiling 92.216 ms/token (§7.2) | DERIVED | R3-05 R2 replay x Phase 0 kappa_low | Clairvoyant; not a causal target; not proven optimal |
| 15 | PP's protected set only shields members of the current event (Observation 1, §7.3) | DERIVED | frozen policy code (`retention_policies.py` `_choose_victim`) | Code-level fact; empirical support by K2 |
| 16 | CES obtains 2.43x PP's byte benefit; PP retired (K2) (§7.3) | CAUSAL_WITHIN_PROTOCOL | exact counterfactual replay, frozen K2 | |
| 17 | K3 survives (62.2% retained) but is moot (§7.3) | DERIVED | R3-05 R3 | |
| 18 | NUE_X 8.79% / 8.11 ms; NUE_LO 4.78% / 4.41 ms; NUE_X about 38x PP's capture (§7.4) | DERIVED | R3-05 R4 EVAL replay, Eq. 2-3 | |
| 19 | NUE variants indistinguishable on DEV; LEX chosen by tie-break, locked before EVAL (§7.4) | DIRECTLY_MEASURED | CANDIDATE_SELECTION.json | |
| 20 | Proposition 1: best tested causal predictive policy fails K4 at zero cost (§7.5) | DERIVED | frozen K4 and EVAL counters | Bounded to the tested family/regime/conversion; not a theorem about causal retention |
| 21 | "Any non-negative policy cost lowers net value below V" (§7.5) | DERIVED | Phase 0 net definition (benefit minus cost) | |
| 22 | NUE_X is the best of the tested causal predictive candidates (§7.5 proof) | DESCRIPTIVE_ONLY | EVAL comparison of the frozen family | Not selected by EVAL; variants tied on DEV |
| 23 | Three conditions (placement, magnitude, cost) each failed (§8.1) | DERIVED | synthesis of K2, K4, R3-04 | Interpretive synthesis |
| 24 | PP's replay value 0.21 ms/token is about 70x smaller than R3-04 bookkeeping cost (§8.3) | DERIVED | Table 3, R3-04 S-A | Compares a replay-converted value with a measured runtime cost |
| 25 | More than 90% of oracle headroom unexplained; causal recoverability unknown (§8.2) | DERIVED / NOT_CLAIMED | 100% - 8.79% | No claim that the remainder is or is not recoverable |
| 26 | Expert usage unpredictable; R2 wrong; all retention impossible; oracle causally reachable; all memory management fails; universal generalization (§9) | NOT_CLAIMED | — | Explicit disclaimers |
| 27 | Oracle-distilled eviction, economic eviction, admission/bypass, prefetch, overlap, scheduling, adaptive allocation, adaptive control (§9) | NOT_CLAIMED (NOT_TESTED) | — | Not failures |
| 28 | Related-work characterizations (§1, §10) | DESCRIPTIVE_ONLY | inherited, previously vetted bibliography | Characterized at title level only; not re-read in this session; **flagged** |

## Sentences checked and changed during drafting
- §2 handoff: changed "the runtime used in that validation did not realize the reference semantics, so that result is not a test" to the audit's own wording ("classified that runtime validation as inconclusive").
- Figure 1 caption: "no stage was re-run after its result was seen" replaced by "no stage's classification rule was changed after its result was seen" (a crashed offline replay was re-executed before its result was read; R3-03B had a protocol-preserving resume).
- §8.3: "two orders of magnitude" replaced by the recomputed ratio (about 70x).
- §4.3: launch roles corrected to "baseline and sham at 4 GiB, baseline at 8 GiB" (A-4GiB is b_selected, B-4GiB is the sham).

## Remaining flags (no number changed)
- Claim 7 is an interpretation from static analysis, labelled as such in the text ("dominant work is algorithmic").
- Claim 28: related-work entries are inherited verbatim from the frozen Paper II bibliography; their content was not re-verified for this paper.
- The economic conversion kappa_low (Eq. 1) is an association; all ms/token values of offline policies inherit that limitation (stated in §3 and §9).

## Correction to a prior conversational report (not to frozen evidence)
The R3-05 R4 conversational report stated NUE_X avoided 2,141 misses and NUE_LO 1,100. The frozen EVAL counters give 3,811 (NUE_X) and 2,100 (NUE_LO) misses avoided vs A (A 106,491; NUE_X 102,680; NUE_LO 104,391). The frozen artifacts and all byte-based results, K4 and K5 were always correct; the paper uses the frozen values.

## Release-preparation edit (nomenclature and citations; no scientific change)
- **Reader-facing names.** Internal repository IDs were removed from the manuscript body, headings, tables, Figure 1 and captions and replaced by descriptive names: R3-03A = Runtime Semantic Equivalence; R3-03B = Predictor Cost Attribution; R3-04 = Real-Runtime Retention Evaluation; R3-05 = Prospective Predictive-Retention Falsification, with R1 = Offline Replay Fidelity, R2 = Clairvoyant Retention Headroom, R3 = Current-Event Shielding, R4 = Causal Next-Use Eviction. The IDs, frozen labels, commits and freeze hashes remain in Appendix A and `SOURCE_MAP.md`. The claim table above keeps the IDs because it is provenance. Table numbers changed (stage table is Table 1, preceding study Table 2, runtime Table 3, mechanism Table 4, scope Table 5); section numbers did not.
- **Preceding study.** The generic "Research 2" handoff was replaced by a compact 13-stage progression (Table 2). Every number in it is checked by `verify_paper.py` against the preceding study's manuscript abstract or claim ledger (claim 1 category unchanged: DIRECTLY_MEASURED in the preceding study, cited not re-measured). The stage "Prediction Benchmark" is stated only as "benchmark contract frozen; development tournament run", the outcome recorded in that study's milestone table.
- **Citations corrected externally (2026-11-22, arXiv records).** ExpertFlow (arXiv:2410.17954 v2): 11 named authors, current title, stated venue DAC '26. LayerScope (arXiv:2509.23638 v2; v1 titled PreScope): 9 named authors, current title, stated venue ICS '26. MoE-Infinity (arXiv:2401.14361 v3): Chuanhao Sun added (6 authors), title unchanged. ProMoE (arXiv:2410.22134): year corrected 2025 -> 2024 (first submission). Venue statements are taken from the arXiv v2 pages of ExpertFlow and LayerScope and were not cross-checked against the publishers. The cache-replacement literature was not added.
- **Unchanged.** All numerical values, confidence intervals, classifications, claim boundaries and the corrected miss counts (NUE_LO 2,100; NUE_X 3,811).
