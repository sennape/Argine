# Research 4 — causal future-distance distillation (CFDC)

**State: CLOSED** (`R4_CFDC_CLOSED`). Final classification: **`R4_CFDC_CAUSAL_STATE_INSUFFICIENT`**. EVAL state CLOSED. No Phase 3, no rescue.

**Manuscript:** *Causal State Is Not Enough: Prospective Evidence That Tested Causal Eviction Policies Recover Too Little Clairvoyant Headroom in Expert Retention* — [`paper/research4/`](../../paper/research4/README.md) ([PDF](../../paper/research4/research4.pdf), pointer in [`papers/research-4`](../../papers/research-4/README.md)). Frozen evidence: [`experiments/R4-00-…`](../../experiments/R4-00-causal-eviction-distillation/README.md), [`R4-01-…`](../../experiments/R4-01-cfdc-training-validation/README.md), [`R4-02-…`](../../experiments/R4-02-cfdc-prospective-eval/README.md).

**Question.** How much of the clairvoyant (Belady) miss-byte headroom can causal eviction policies recover, and can a rich causal decision rule be distilled into a cheap deployable rule (a CART of depth ≤ 3 with ≤ 4 integer features)? Research 3 found that the best causal next-use policy captured only 8.79% of the headroom on its canonical stream and that prediction-led retention had no runtime value; it left open whether richer causal state would change that.

**Frozen protocol.** Offline exact replay of Qwen3-30B-A3B-Q4_K_M routing traces at the 1.5 GiB budget; endpoint `capture(P) = Σ(B_A − B_P) / Σ(B_A − B_Belady)` (decode miss bytes, pooled, exact rational, uncapped). Baseline A: aged-frequency policy. Comparators: NUE_X (the best causal next-use policy of Research 3) and DECAYED_COUNT (a simple recency–frequency rule). RICH: gradient-boosted regression trees over 16 integer causal features (diagnostic information ceiling). CHEAP: depth-3 CART distilled from the same labels. Decision rules A (CHEAP ≥ 25% on validation and EVAL), B (EVAL ≥ max(25%, best comparator + 3 pp)), C (non-negative on every EVAL workload) and the RICH gate (RICH ≥ 25% on both splits).

**Splits.** TRAIN: 4 development traces (one per workload category, seed 42; the policies are fitted on these only). VALIDATION: 12 traces (4 categories × 3 versions from the Research 2 predictive-retention stage), scored once. EVAL: 4 sealed prospective traces (one per category), opened once after the models and validation scores were locked. Raw traces are not in Git.

| split | NUE_X | DECAYED_COUNT | RICH | CHEAP |
|---|---|---|---|---|
| VALIDATION (12 traces) | +7.42% | −5.60% | +15.45% | −36.04% |
| EVAL (4 traces) | +9.37% | +3.05% | +19.11% | −29.96% |

CHEAP is negative on all 16 traces (it incurs more miss bytes than A); no policy reaches 25% on any trace. The recoverable headroom is 41.63% (validation) and 41.85% (EVAL) of A's miss bytes. Rules A, B, C and the RICH gate all fail, so the classification is `R4_CFDC_CAUSAL_STATE_INSUFFICIENT`.

| phase | directory | outcome |
|---|---|---|
| Phase 0 — protocol freeze | [`R4-00-causal-eviction-distillation`](../../experiments/R4-00-causal-eviction-distillation/README.md) | protocol, splits, models and decision rules frozen before any result; EVAL locked |
| Phase 1 — training and prospective validation | [`R4-01-cfdc-training-validation`](../../experiments/R4-01-cfdc-training-validation/README.md) | CHEAP and RICH fitted on TRAIN; validation captures as above |
| Phase 2 — prospective evaluation and classification | [`R4-02-cfdc-prospective-eval`](../../experiments/R4-02-cfdc-prospective-eval/README.md) | EVAL opened once; captures as above; rules A, B, C and the RICH gate fail |

**Relation to Research 2 and 3.** Research 2 established the headroom and the causal predictive structure; Research 3 found no runtime value and attributed the shortfall to policy quality plus cost; Research 4 isolates the representation question with exact replay and no policy-computation cost, and finds that the tested causal state, feature vocabulary and model classes do not recover enough of the headroom.

**Claim boundary.** Within the frozen feature vocabulary, model families (one gradient-boosted tree configuration, one depth-3 tree) and regime (one model, runtime lineage and budget, offline replay), no tested causal policy meets the preregistered sufficiency level, and the distilled cheap rule is worse than the baseline. This does not show that causal or learned eviction is impossible, that other features, learners, iterative imitation or other models/hardware would not recover more, or that Belady is a realistic target. Overhead is not the explanation: the replay charges no computation. See [`CLAIM_BOUNDARIES.md`](../../experiments/R4-02-cfdc-prospective-eval/CLAIM_BOUNDARIES.md).

**Future-work boundary.** Research 5 is not opened and nothing is specified or authorized. Mechanisms outside eviction quality (hiding transfer latency, scheduling, placement, reducing the bytes moved per miss) are untested directions; none is begun here.

Frozen identities: Phase 0 commit `35410947d51895326aa7e6c39a09825e2ffb4c0c` (digest `fa0ff8d4c93b463a99bdba4a65625251fa00c8102f5b2d788f8f22e73a20d64e`); Phase 1 final commit `e46df73992ec64e829eddce63e3d50e65bdbd434` (contract digest `8d89ecd8198db93000456cde58f995e4fa23124d554402b61170a776d9a0e74f`); Phase 2 opening commit `30e37fdb0063ed96fa9b9a2d9350bf71a538830e`, closure commit `a9f92f2e06eacc2d52920f972739e19936074632` (contract digest `1b690816e6625333fbcac5bf188934ef2f51dbf8ab82ecd1ae7b59c3f02065e0`, freeze SHA `fc411ef578decf6f7f380832885651f278d8696d9f46d60d2c9d10dce0cb1fdd`). These commits are not part of this repository's public history (they sit on a history with oversized R2-12 blobs); the frozen artifacts and digests above are verified instead — see [`manifests/consolidation-path-changes.json`](../../manifests/consolidation-path-changes.json).

## Verification

```
python shared/tooling/run_frozen_verifier.py experiments/R4-00-causal-eviction-distillation/phase0.py verify
python shared/tooling/run_frozen_verifier.py experiments/R4-01-cfdc-training-validation/phase1.py verify
python shared/tooling/run_frozen_verifier.py experiments/R4-02-cfdc-prospective-eval/phase2.py verify
python shared/tooling/run_frozen_verifier.py paper/research4/scripts/verify_paper.py
```

## Omitted raw artifacts

The raw routing traces behind TRAIN (4), VALIDATION (12) and EVAL (4) are not in Git; they are identified by SHA-256 in [`DATA_SPLITS.json`](../../experiments/R4-00-causal-eviction-distillation/DATA_SPLITS.json) and summarized in [`manifests/raw-artifacts-omitted.json`](../../manifests/raw-artifacts-omitted.json). Re-running the scoring needs those traces and the Phase 1 fitted models (included).
