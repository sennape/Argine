# R2-05 -- Expert Frequency and Concentration

**Phase:** II-B (first experiment)
**Depends on:** R2-04 (Research II-A must be CLOSED/PASS)

## Scientific purpose

Characterize how concentrated expert routing is on the validated real
30B MoE trace (R2-01), per MoE layer. This is purely DESCRIPTIVE
characterization -- it does not answer which expert follows which
(R2-06), how soon an expert is reused (R2-07), how routing changes over
time/workloads (R2-08), or whether routing is predictable (II-C), and
must not be read as doing so.

Sub-questions (section 2 of the task):

1. Are expert requests distributed approximately uniformly within layers?
2. Do a relatively small number of experts account for a large fraction
   of requests?
3. How much does concentration vary across MoE layers?
4. What is the effective number of active experts compared with the
   nominal expert count?
5. Is the observed routing frequency distribution consistent with a
   small hot subset carrying disproportionate traffic?

A nearly uniform result is a valid, publishable finding, not a null
result -- R2-05 does not assume in advance that concentration is strong.

## Nature of this experiment

Fully offline. No new trace capture, no model weights loaded, no
`llama-server` invocation. R2-05 reads only the exact validated R2-01
trace (re-hashed and bound dynamically, never a hard-coded path or SHA)
and derives everything from it in-process.

## Request unit (critical, documented choice)

One counted expert request = one occurrence of one expert id within one
token's selected-expert tuple (`Trace.token_selections(layer, phase)`),
NOT the per-step-per-layer cache-residency union R2-01..R2-04 use. See
`docs/research2/METHODOLOGY.md`'s R2-05 section and
`argine/research2/r2_05/request_unit.py`'s module docstring for the full
rationale.

## Expert identity (critical invariant)

Expert identity is strictly **(layer_id, expert_id)**. Expert 17 in
layer 5 is never the same logical expert as expert 17 in layer 31. No
code path in `argine/research2/r2_05/` ever merges counts for the same
raw `expert_id` across different layers.

## Terminology discipline

Allowed: "upper bound", "headroom" (when discussing the II-A bridge),
"frequency concentration", "information headroom" (II-A only, not
R2-05's own metric). Hot experts are defined RANK-based (top-1/4/8/16
within a layer), never by an arbitrary post-hoc threshold.

Forbidden from any R2-05 SUPPORTED claim: "Zipfian" / "power-law" /
"heavy-tailed" without a distributional fit test (R2-05 never performs
one); "these experts remain hot over time" (a temporal-stability claim --
R2-08's job); "frequency concentration explains the R2-04 Oracle gap" or
"frequency alone is sufficient" for retention (see the LFU bridge below).

## The LFU bridge (why concentration alone is not enough)

R2-02 already showed LFU performing WORSE than Global-LRU at the 4 GiB
budget. This is direct evidence that simple global request-frequency
information is not, by itself, sufficient to drive a good retention
policy. So even if R2-05 finds strong frequency concentration, the
correct interpretation is: aggregate frequency structure may exist in
the routing stream, while frequency alone does not capture the TIMING
information the Oracle exploits. This motivates R2-06 (transition
structure) and R2-07 (reuse distance), not a frequency-only retention
policy.

## Artifacts

Under the Research2 evidence root, per run:
`summary.json`, `summary.md`, `provenance.json`, `manifest.json`,
`expert_frequency.csv`, `layer_concentration.csv`, `concentration.json`,
`claims.json`, `figure_data.json`.

## Automation

```
argine.bat research2 run R2-05
```

One command: verifies II-A is CLOSED/PASS, verifies trace identity,
loads the validated R2-01 trace, computes frequency tables and
concentration metrics, classifies the outcome, persists evidence. No
manual data extraction step.

## Roadmap state after a R2-05 PASS

- Research II-A: CLOSED / PASS / PAPER-FROZEN (unchanged)
- R2-05: COMPLETED (not auto-started before a human runs it)
- R2-06 / R2-07 / R2-08: LOCKED (not auto-started)
- Research II-C: FUTURE
