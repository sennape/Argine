# R3-03A runtime semantic correction

Status: **R3_03A_SEMANTIC_CORRECTION_IMPLEMENTED_OFFLINE**. Runtime semantic conformance is NOT claimed; it requires the host run described in `CONFORMANCE_PLAN.json`. No timing evidence exists or is claimed.

## What changed
The frozen R3 runtime called `policy->access()` once per `MUL_MAT_ID` node (gate, up, down), i.e. 3 policy events per (forward pass, MoE layer), 144 per decode token, whereas the R2 reference defines one event per (pass, layer), 48 per decode token. The audit (`experiments/R3-03-event-semantics-audit`) found `R2_R3_EVENT_SEMANTICS_NOT_EQUIVALENT`.

The corrected runtime separates:
- **physical** availability: gate/up/down experts are still ensured and pinned for every node (follow-up nodes are served without policy access or eviction);
- **semantic** policy/prediction state: advanced exactly once per (pass, layer), at the first node of the event. A second event for the same (pass, layer) is refused fail-closed before any state changes.

Trace rows gain an additive `semantic_event` block; legacy rows and the legacy entry point are unchanged.

## Files
- `patches/argine-store.h`, `argine-store.cpp`, `argine-experts.cpp` (modified), `patches/argine-semantic-event-test.cpp` (new)
- `tools/r3_03a_conformance.py` (real-model conformance driver + offline-tested analyser)
- `tests/test_semantic_correction.py` (14 tests), `replay_frozen_triplets.py`, `FROZEN_TRIPLET_REPLAY_B-4GiB.json`
- `IMPLEMENTATION.json`, `CONFORMANCE_PLAN.json`, `REGRESSION_CLASSIFICATION.json`, `FREEZE.json`

## Evidence
- Frozen R3 B-4GiB trace: 74,016 physical rows -> 24,672 semantic events (514 passes x 48 layers, ratio 3.0). The frozen R2 reference and a C++ mirror agree on all 24,672 events; the logged original `protected_before` agrees on only 1,845 (7.48%).
- Offline conformance against the frozen R2 reference classes on a tiny store: b_selected, predicted_protect and sham.
- New runtime (host build, exit 0): SHA-256 `e935005e...fd76`, differs from the frozen runtime `3f094f5b...bbd644`, which was preserved.
- Build-only UI-asset workaround: UI assets copied from the frozen build (not scientific source).

## Regressions
No genuine R3-03A runtime regression was identified; the 2 R3-03 audit tests are historical-source tests bound to the pre-correction source and are intentionally unchanged. See `REGRESSION_CLASSIFICATION.json`.
