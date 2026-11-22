# CES — Current-Event Shield (pre-registered before any R3 number was computed)

CES = frozen `AgedFrequency` b_selected (1.5 GiB: decay 0.999, aging interval 1) plus ONE rule: while serving an event at layer L, residents (L, e) with e in that event's own requested set are excluded from victim candidates. If no other candidate exists, the unrestricted frozen choice is used (total fallback, counted).
No predictor, no history, no future knowledge, no last-observation, no cross-layer state, no lookahead, no new heuristic. The shield is rebuilt from the event's own `req` at each access and cleared after it.

## Pre-registered metrics
- Effect metric (K2 and K3): decode miss bytes avoided vs A (decode = event index >= 96, 512 tokens); decode misses reported alongside.
- K2: ratio = CES bytes avoided / full-PP bytes avoided (exact fractions); ratio >= 0.8 => R3_05_K2_RETIRE_CURRENT_PP, else R3_05_K2_PP_HAS_ADDITIONAL_EFFECT.
- K3: retained = (A bytes - PP(N=10) bytes) / (A bytes - PP(N=1) bytes), f = 1/10 exactly; retained <= 0.2 => R3_05_K3_FREQUENCY_FAMILY_DROP (exactly 0.2 drops), else SURVIVES (not a success).
- PP_CURVE refresh rule: per layer, history always appended; predict() called only when the layer's event position i satisfies i % N == 0; protected set held between refreshes. N=1 must equal frozen PP exactly. Phase 0 wording is "same-layer events"; the prompt says "semantic events"; one semantic event is one layer event here, so the two coincide per layer.
- Thresholds, N grid and K3 point were not altered after seeing results.
