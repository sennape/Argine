"""R2-03 -- Expert-Memory Budget Sensitivity.

An offline replay experiment, on the exact same R2-01-validated real
trace R2-02 consumed, that measures whether the Oracle-vs-causal-
retention gap R2-02 observed at 4 GiB is robust across expert-memory
budgets. Still not a runtime-performance experiment: no latency,
throughput, wall-clock, energy, or hardware-performance claim is made
anywhere. Does not yet define the final "recoverable margin" (R2-04's
job); emits the canonical per-budget data R2-04 will consume.

See docs/research2/METHODOLOGY.md ("R2-03: Expert-Memory Budget
Sensitivity") and this experiment's README for the full design, the
predeclared budget sweep and its rationale, and the scientific-outcome
rules.
"""
