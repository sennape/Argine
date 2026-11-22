# Research Part 2 — overview

Argine2 is the frozen Research Part 1 repository. Its Phase 1 observation
campaign and publication artifact are closed; it is not developed further.
Argine3 is its Research Part 2 descendant: a Git clone of Argine2's full
history (not a filesystem copy), branched at the frozen baseline.

- **Baseline SHA:** `406763361a49a2c7ab6cbb811655dbdc51e77f9b` (Argine2
  `master`, commit "Document public snapshot provenance"). See
  [`BASELINE.md`](BASELINE.md) for the evidence used to identify it.
- **Branch:** Research Part 2 development happens on `r2-main`, created at
  that baseline. The old Argine2 `master` branch is not developed further
  inside Argine3.
- **Relationship with HoPi:** HoPi is a separate product/runtime consumer.
  It is out of scope for this repository and for Research Part 2; no
  Research Part 2 finding is integrated into HoPi by this infrastructure,
  and none will be until a human decision does so deliberately, elsewhere.

## Canonical chain: II-A -> II-B -> II-C

1. **II-A — Oracle Expert Retention.** How improvable is expert retention
   in theory? (R2-01..R2-04)
2. **II-B — Routing Characterization.** Is expert routing sufficiently
   structured/predictable? (R2-05..R2-08)
3. **II-C — Working-Set Prediction.** Can a causal predictor exploit that
   structure to improve real runtime behaviour? (R2-09..R2-12)

Prediction, prefetching and phase-awareness are mechanisms/subactivities
inside II-C, not separate canonical phases.

See [`METHODOLOGY.md`](METHODOLOGY.md) for the evidence/provenance rules and
[`AUTOMATION.md`](AUTOMATION.md) for the `argine research2 ...` commands.

**R2-M0.1 scientific state:** infrastructure only. Zero Research Part 2
experiments have been run. Zero scientific findings exist yet.
