# Research 4 — causal future-distance distillation (CFDC)

**State: CLOSED** (`R4_CFDC_CLOSED`). Final classification: **`R4_CFDC_CAUSAL_STATE_INSUFFICIENT`**. EVAL state CLOSED. No Phase 3, no rescue.

**Question.** How much of the clairvoyant expert-eviction optimum can a cheap causal policy recover using only information available at the eviction decision, when distilled from the clairvoyant teacher?

| phase | directory | outcome |
|---|---|---|
| Phase 0 — protocol freeze | [`R4-00-causal-eviction-distillation`](../../experiments/R4-00-causal-eviction-distillation/README.md) | protocol, splits, models and decision rules frozen before any result; EVAL locked |
| Phase 1 — training and prospective validation | [`R4-01-cfdc-training-validation`](../../experiments/R4-01-cfdc-training-validation/README.md) | CHEAP and RICH fitted on TRAIN; VALIDATION: CHEAP −36.04%, RICH +15.45%, NUE_X +7.42%, DECAYED_COUNT −5.60% (capture of the A→Belady miss-byte headroom) |
| Phase 2 — prospective evaluation and classification | [`R4-02-cfdc-prospective-eval`](../../experiments/R4-02-cfdc-prospective-eval/README.md) | EVAL (4 traces, opened once): CHEAP −29.96%, RICH +19.11%, NUE_X +9.37%, DECAYED_COUNT +3.05%; rules A, B, C and the RICH gate all fail |

**Interpretation boundary.** CHEAP failing means the frozen cheap causal rule was not demonstrated viable. RICH failing means the frozen causal feature representation did not show sufficient generalizable information for the preregistered target. Neither proves that all causal eviction, all MoE retention or all learned eviction is impossible, or that other hardware or regimes behave identically. See [`CLAIM_BOUNDARIES.md`](../../experiments/R4-02-cfdc-prospective-eval/CLAIM_BOUNDARIES.md).

Frozen identities: Phase 0 commit `35410947d51895326aa7e6c39a09825e2ffb4c0c` (digest `fa0ff8d4c93b463a99bdba4a65625251fa00c8102f5b2d788f8f22e73a20d64e`); Phase 1 final commit `e46df73992ec64e829eddce63e3d50e65bdbd434` (contract digest `8d89ecd8198db93000456cde58f995e4fa23124d554402b61170a776d9a0e74f`); Phase 2 opening commit `30e37fdb0063ed96fa9b9a2d9350bf71a538830e`, closure commit `a9f92f2e06eacc2d52920f972739e19936074632` (contract digest `1b690816e6625333fbcac5bf188934ef2f51dbf8ab82ecd1ae7b59c3f02065e0`, freeze SHA `fc411ef578decf6f7f380832885651f278d8696d9f46d60d2c9d10dce0cb1fdd`). These commits are not part of this repository's public history (they sit on a history with oversized R2-12 blobs); the frozen artifacts and digests above are verified instead — see [`manifests/consolidation-path-changes.json`](../../manifests/consolidation-path-changes.json).

## Verification

```
python shared/tooling/run_frozen_verifier.py experiments/R4-00-causal-eviction-distillation/phase0.py verify
python shared/tooling/run_frozen_verifier.py experiments/R4-01-cfdc-training-validation/phase1.py verify
python shared/tooling/run_frozen_verifier.py experiments/R4-02-cfdc-prospective-eval/phase2.py verify
```

## Omitted raw artifacts

The raw routing traces behind TRAIN (42), VALIDATION (12) and EVAL (4) are not in Git; they are identified by SHA-256 in [`DATA_SPLITS.json`](../../experiments/R4-00-causal-eviction-distillation/DATA_SPLITS.json) and summarized in [`manifests/raw-artifacts-omitted.json`](../../manifests/raw-artifacts-omitted.json). Re-running the scoring needs those traces and the Phase 1 fitted models (included).
