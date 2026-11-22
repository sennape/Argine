# R3 event-granularity audit

Classification: `R2_R3_EVENT_SEMANTICS_NOT_EQUIVALENT`. Recommended direction:
`R3_03_RUNTIME_SEMANTIC_CORRECTION_FIRST`.

R2 defines one policy event per (forward pass, layer) over the union expert set. The R3 runtime
calls `policy->access()` once per MUL_MAT_ID node (gate, up, down), i.e. 3 events per (pass, layer)
with identical sets (74,016 actual vs 24,672 semantic events in the 4 GiB B trace). The C++ predictor
itself reproduces the R2 reference exactly on the logged stream; the divergence is the event stream
(source: `patches/argine-experts.cpp` compute_forward → `patches/argine-store.cpp` servi_evento_con_politica).
Effects: predictor state/decay/transition counts, base-policy aging, ~3x bookkeeping calls, and
re-misses at calls 2–3 at 4/8 GiB. R2-10/R2-11 unaffected; R2-12 inconclusive; R3-02 valid for the realised runtime.

Offline only; no model was run; R3-02G Session 1 timing was not read.

Reproduce:
    zstd -dc B-4GiB.zst | python tools/r3_event_semantics_audit.py extract - rows.jsonl
    python tools/r3_event_semantics_audit.py analyze rows.jsonl audit.json
    python experiments/R3-03-event-semantics-audit/build_artifacts.py <dir with audit_B15/B4/B8.json>
    python3 -m unittest experiments/R3-03-event-semantics-audit/tests/test_event_semantics.py

Caveats are listed in `AUDIT.json`. The R3-03 experimental protocol is intentionally not created here.
