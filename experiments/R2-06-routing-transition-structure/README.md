# R2-06 -- Routing Transition Structure

**Phase:** II-B (second experiment)
**Depends on:** R2-05 (must be PASS)

## Scientific purpose

Characterize whether the SEQUENCE of layer-local expert selections
carries conditional/transition structure beyond R2-05's marginal
frequency finding -- on the validated real 30B MoE trace (R2-01), per
MoE layer, SAME-LAYER only. This is purely DESCRIPTIVE characterization
-- it does not measure reuse distance (R2-07), temporal/windowed
workload drift (R2-08), or routing predictability (II-C), and it must
not be read as doing so. Cross-layer transitions are explicitly
excluded from the primary analysis: layer visitation order is
architectural (trivially determined by the trace's own fixed layer
order), not a scientific routing transition.

Sub-questions:

1. Does observing the current source expert change the probability
   distribution over the next same-layer target expert, relative to
   that target's own marginal frequency (R2-05)?
2. How much same-layer transition structure exists, measured as mutual
   information between consecutive routing decisions?
3. Are self-transitions (an expert routing to itself) common, rare, or
   neither -- measured, never assumed?
4. How much of any observed structure survives a predeclared minimum-
   support filter, versus appearing only in rare, low-confidence cells?

A null result (no detectable same-layer transition structure anywhere)
is a valid, publishable finding -- R2-06 does not assume in advance that
transition structure exists.

## Nature of this experiment

Fully offline. No new trace capture, no model weights loaded, no
`llama-server` invocation. R2-06 reads only the exact validated R2-01
trace (re-hashed and bound dynamically, never a hard-coded path or SHA)
and R2-05's own canonical frequency evidence (also bound dynamically),
and derives everything from them in-process.

## Transition unit (critical, documented choice)

One counted transition observation = one (source_expert_id,
target_expert_id) pair contributed by one pair of CONSECUTIVE entries
`Trace.token_selections(layer, phase)` yields for one layer --
token-to-token, SAME layer. "Consecutive" means consecutive in the
iterator's own yielded order, never consecutive token_index. Multi-
expert (top-k) selections are expanded via documented pairwise
set-to-set counting, every pair weighted 1, never deduplicated. See
`docs/research2/METHODOLOGY.md`'s R2-06 section and
`argine/research2/r2_06/transition_unit.py`'s module docstring for the
full rationale, including why cross-layer and step-to-step candidate
units were not chosen as primary.

## Expert identity (critical invariant)

Expert identity is strictly **(layer_id, expert_id)**, inherited
unchanged from R2-05. No code path in `argine/research2/r2_06/` ever
merges transition counts for the same raw `expert_id` across different
layers.

## Terminology discipline

Allowed: "transition structure", "mutual information" / "normalized
mutual information" (an association measure), "lift", "self-transition
lift/share". Forbidden from any R2-06 SUPPORTED claim: any predictor
accuracy/precision/recall/F1 language (R2-06 trains no predictor --
predictability is II-C's question); "reuse distance" findings (R2-07's
job); "temporal drift" / "windowed drift" findings (R2-08's job); a
claim that cross-layer transitions explain the observed same-layer
structure (R2-06's primary analysis is SAME-LAYER only); a claim that
self-transitions dominate the routing process (measured, never assumed).

## The R2-05 bridge (why this is NOT a frequency recap)

R2-06's transition-derived target marginal is compared, per layer and
per expert, against R2-05's own request-unit marginal. Exact equality
is never required -- the two come from different unit definitions (R2-06's
pairwise-expanded transition unit structurally drops each layer's first/
last token and multiplies interior contributions by surrounding
selection sizes) -- only a gross, structural divergence is flagged, and
never as a hard FAIL condition by itself. A positive R2-06 finding does
not retroactively validate a frequency-only retention policy (R2-02
already showed LFU losing to Global-LRU at 4 GiB) and does not by
itself establish that any transition structure found here is
exploitable by a real policy.

## Predeclared support threshold

`MIN_SOURCE_SUPPORT = 30`, `MIN_PAIR_SUPPORT = 10` -- fixed BEFORE any
real R2-06 execution, based on conservative sample-count conventions,
not tuned to any observed result. Applied only to headline/strong-
transition summaries; raw per-cell evidence in `transition_metrics.csv`
is never filtered.

## Artifacts

Under the Research2 evidence root, per run:
`summary.json`, `summary.md`, `provenance.json`, `manifest.json`,
`transition_metrics.csv`, `layer_transition_summary.csv`,
`transition_structure.json`, `null_baseline.json`, `claims.json`,
`figure_data.json`.

## Automation

```
argine.bat research2 run R2-06
```

One command: verifies R2-01 PASS and R2-05 PASS, verifies trace
identity, loads the validated R2-01 trace and the R2-05 bridge evidence,
computes the transition matrix, information metrics, null baselines,
concentration/self-transition metrics, and the global summary,
classifies the outcome, persists evidence. No manual data extraction
step.

## Roadmap state after a R2-06 PASS

- Research II-A: CLOSED / PASS / PAPER-FROZEN (unchanged)
- R2-05: CLOSED / PASS (unchanged)
- R2-06: COMPLETED (not auto-started before a human runs it)
- R2-07 / R2-08: LOCKED (not auto-started)
- Research II-C: FUTURE
