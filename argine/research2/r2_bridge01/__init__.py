"""R2-BRIDGE-01 -- Retention Feasibility Bridge.

A new, separate, prospective protocol created AFTER R2-10's formal closure
(commit e13d89f, r2-main). It does not reopen, reinterpret, or modify R2-10.

R2-10's held-out EVAL established (Category B, SUPPORTED by held-out
evidence) that the frozen causal predictor decayed_transition/decay=0.99
has measurable future-expert-recall advantage over trivial baselines, but
that advantage was never evaluated as a retention/eviction signal -- R2-10
only ever scored *prediction* quality (recall@k against the next routing
event), never *retention* quality (whether using the prediction to decide
what stays resident reduces miss_bytes against a real eviction policy).

R2-BRIDGE-01 asks exactly that bridging question, under its own frozen
Phase-0 contract, with its own new held-out corpus (never the R2-10 EVAL
set), and explicitly cannot by itself re-trigger or satisfy the R2-10
decision rules. See experiments/R2-BRIDGE-01-retention-feasibility/README.md.
"""
