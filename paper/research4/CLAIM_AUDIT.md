# Research 4 paper: claim audit

Classification used in Section 9 of the paper. Every numerical claim is in `SOURCE_MAP.md` and checked by `scripts/verify_paper.py`.

| Status | Statement | Basis |
|---|---|---|
| Established | No tested causal policy (two hand-built, one rich learned, one distilled cheap) captures 25% of the clairvoyant miss-byte headroom on both the validation cohort and the prospective held-out set | exact per-trace miss bytes; Proposition 1 |
| Established | The distilled cheap policy incurs more miss bytes than the baseline on all 16 traces (−36.04% validation, −29.96% held-out) | per-trace table; Rules A and C fail |
| Established | The rich learned policy recovers about twice the best hand-built comparator (15.45% / 19.11% versus 7.42% / 9.37%) and still falls 9.55 / 5.89 percentage points short | pooled captures |
| Established | The shortfall is not explained by policy-computation overhead (none is charged in exact replay) | design of the endpoint |
| Made less plausible | The tested causal feature vocabulary with a boosted-tree learner is enough to recover most of the clairvoyant headroom; the tested distillation route yields a deployable policy that preserves the rich policy's value; overhead alone explains the earlier negative runtime result | results above |
| Unknown | Whether other causal state, other learners, iterative imitation under the learner's own states or other training signals recover more; how much headroom is causally attainable at all; behaviour on other models, hardware, budgets and workloads | outside the frozen regime |
| Not shown | That every causal or learned eviction policy is impossible; that Belady is a realistic or optimal target for multi-key two-size events; that expert retention is fundamentally impossible; that other mechanisms (transfer hiding, scheduling, placement) cannot give speedups | not tested |
| Non-gating | Bootstrap intervals, sensitivity excluding the canonical-stream trace, teacher-agreement diagnostic (12.46% on the baseline trajectory vs 3.76% under the cheap policy's own states). The cause of the collapse was not isolated | described as such in Sections 5, 6 and 7.3 |
