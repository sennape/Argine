# R2-07 -- Reuse Distance and Working-Set Structure

**Phase:** II-B (third experiment)
**Depends on:** R2-06 (must be PASS)

## Scientific purpose

Characterize how far layer-local expert reuse locality extends beyond
R2-06's one-step conditional structure, and how quickly the active
expert working set grows as the observation window widens -- on the
validated real 30B MoE trace (R2-01), per MoE layer, SAME-LAYER only.
This is purely DESCRIPTIVE characterization -- it does not train a
predictor, does not report predictor accuracy, does not measure
temporal/windowed workload drift or cross-workload/cross-model
generalization (R2-08's and II-C's job), and does not convert any
reuse-distance finding into a retention-policy performance claim.

Sub-questions:

1. When a layer-local expert is reused, how far away (in token-
   selection positions) was it last seen -- the token reuse gap -- and
   separately, how many DISTINCT other experts intervened?
2. How large is the rolling active working set at each of nine
   predeclared window sizes, and how fast does it grow as the window
   widens?
3. Does R2-06's one-step conditional structure extend to a measurable
   multi-step reuse horizon, or does it decay quickly?
4. Is being a frequently-requested expert (R2-05) the same property as
   being reused soon (R2-07), or are they distinct?
5. How does the observed reuse/working-set structure compare to a
   frequency-preserving, order-destroyed (shuffled) null baseline?

A null result (no detectable reuse locality anywhere, or a working set
that grows as fast as the shuffled null) is a valid, publishable
finding -- R2-07 does not assume in advance that reuse locality exists.

## Nature of this experiment

Fully offline. No new trace capture, no model weights loaded, no
`llama-server` invocation. R2-07 reads only the exact validated R2-01
trace (re-hashed and bound dynamically, never a hard-coded path or SHA)
and R2-05's and R2-06's own canonical evidence (also bound dynamically),
and derives everything from them in-process.

## Temporal unit (critical, documented choice)

The same `Trace.token_selections(layer, phase)` accessor R2-05 and
R2-06 already validated, re-numbered 0, 1, 2, ... in the ITERATOR'S OWN
yielded order (never raw `token_index`). A REUSE EVENT occurs when an
expert seen at position `t_now` was last seen (same layer) at an
earlier position `t_prev`; `token_reuse_gap = t_now - t_prev`. A
SEPARATE metric, `distinct_intervening_reuse_distance`, counts the
distinct experts seen at any position strictly between `t_prev` and
`t_now`, excluding the reusing expert -- the two metrics are never
conflated. No within-token-tuple ordering is ever invented. See
`docs/research2/METHODOLOGY.md`'s R2-07 section and
`argine/research2/r2_07/temporal_unit.py`'s module docstring for the
full rationale, including the duplicate-expert-within-one-token
accounting rule.

## Expert identity (critical invariant)

Expert identity is strictly **(layer_id, expert_id)**, inherited
unchanged from R2-05/R2-06. No code path in `argine/research2/r2_07/`
ever merges reuse events or working-set membership for the same raw
`expert_id` across different layers.

## Terminology discipline

Allowed: "token reuse gap", "distinct intervening reuse distance",
"working-set size" (always a plain expert COUNT, never bytes/GiB
unless a clearly separated secondary analysis with genuine per-expert-
size metadata is added -- not done here), "structurally favorable to
retention" (descriptive, non-performance). Forbidden from any R2-07
SUPPORTED claim: any predictor accuracy/precision/recall/F1/AUC
language (R2-07 trains no predictor); "this policy would reduce misses
by X%" (actual retention-policy performance is II-C's job); "temporal
drift" / "windowed drift" / "workload drift" findings (R2-08's job); a
cross-workload or cross-model generalization claim ("MoE models
generally...", "Qwen workloads generally..."); and a claim that R2-06
already proved long-range locality (R2-07 is what tests that, for the
first time).

## The R2-06 bridge (why this is NOT a transition recap)

R2-06 identified ONE-STEP conditional routing structure. R2-07 measures
the temporal horizon and working-set consequences of that structure --
R2-06's finding does not by itself establish long-range locality;
R2-07 is what tests that. The canonical digest explicitly binds R2-06's
own run id and canonical digest, not merely R2-06's existence as a PASS
prerequisite.

## The R2-05 bridge (frequency vs. reuse-soon)

Being FREQUENT (R2-05) is a logically distinct property from being
REUSED SOON (R2-07). R2-07 computes an optional, SECONDARY, descriptive
Spearman rank correlation between per-expert request frequency and
median token reuse gap, implemented from scratch (no scipy dependency),
with no causal interpretation attached.

## Predeclared thresholds, windows, and quantile method

`TOKEN_GAP_THRESHOLDS = (1, 2, 4, 8, 16, 32, 64, 128, 256)`,
`DISTINCT_INTERVENING_THRESHOLDS = (4, 8, 16, 32, 64)`, and
`CANONICAL_WINDOWS = (1, 2, 4, 8, 16, 32, 64, 128, 256)` (with `512` as
an optional, non-headline secondary window) -- all fixed BEFORE any
real R2-07 execution, based on round-number conventions, not tuned to
any observed result. Working-set windows use FULL-WINDOWS-ONLY
semantics: a layer with fewer observed positions than a window
contributes no windows of that size at all, never a padded or
truncated partial window. The quantile method is linear interpolation
between order statistics (NumPy/R "linear" convention), predeclared
and verified against known reference values.

## Frequency-preserving null baseline

A single, fixed-seed (`NULL_BASELINE_SEED = 1234`) WHOLE-TUPLE SHUFFLE
of each layer's own position sequence -- never splitting or
individually reordering experts within one token's selection -- which
provably preserves token count, per-token cardinality, total
occurrences, and per-expert marginal counts; these invariants are
re-verified programmatically every run, fail-closed on any violation.

## Artifacts

Under the Research2 evidence root, per run:
`summary.json`, `summary.md`, `provenance.json`, `manifest.json`,
`reuse_events.csv`, `layer_reuse_summary.csv`, `working_set_by_window.csv`,
`reuse_structure.json`, `null_baseline.json`, `bridge.json`,
`claims.json`, `figure_data.json`.

## Automation

```
argine.bat research2 run R2-07
```

One command: verifies R2-01 PASS, R2-04/II-A PASS, R2-05 PASS and R2-06
PASS (the full upstream chain), verifies trace identity, loads the
validated R2-01 trace and the R2-05/R2-06 bridge evidence, computes
reuse events, reuse-distance distributions, rolling working-set
metrics and growth curves, the frequency-preserving null baseline, and
the global summary, classifies the outcome, persists evidence. No
manual data extraction step.

## Roadmap state after a R2-07 PASS

- Research II-A: CLOSED / PASS / PAPER-FROZEN (unchanged)
- R2-05 / R2-06: CLOSED / PASS (unchanged)
- R2-07: COMPLETED (not auto-started before a human runs it)
- R2-08: LOCKED (not auto-started)
- Research II-C: FUTURE
