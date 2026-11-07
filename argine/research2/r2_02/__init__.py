"""R2-02 -- Oracle vs Retention Baselines.

A pure offline retention/replay comparison on the already-validated R2-01
real candidate trace: how much better can a non-causal, future-aware
(Oracle) retention policy perform than causal retention baselines, at the
fixed 4 GiB budget R2-01 validated. This is explicitly NOT a runtime-
performance experiment -- no latency, throughput, wall-clock, energy, or
hardware-performance claim is made anywhere in this package.

R2-02 consumes R2-01's canonical PASS evidence as a read-only upstream
prerequisite (see prerequisite.require_r2_01_pass) and never modifies or
re-captures it. See docs/research2/METHODOLOGY.md ("R2-02: Oracle vs
Retention Baselines") and this experiment's README for the full design,
policy definitions, and scientific-outcome rules.
"""
