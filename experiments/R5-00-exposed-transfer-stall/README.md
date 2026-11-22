# R5-00 — Exposed Transfer Stall (ETS): Phase 0 protocol freeze

**State: `R5_ETS_PHASE0_FROZEN`.** Evaluation: **LOCKED**. Implementation: **NOT STARTED**. No runtime was launched, no evaluation prompt was run, and no outcome metric exists.

**Question.** Does a causal deferred-wait scheduler reduce the expert-transfer wait exposed on the decode critical path enough to lower end-to-end decode time per token by at least 2% at 1.5 GiB? The scheduler waits for each expert-weight kind at the node that consumes it, using only the current layer's router decision. It adds no prediction and leaves the bytes read and the residency decisions unchanged. The exact wording is in [`PROTOCOL.json`](PROTOCOL.json).

**Why.** The runtime audit ([`RUNTIME_AUDIT.json`](RUNTIME_AUDIT.json)) found that the first expert node of each layer issues the reads for gate, up and down. Every thread then waits for all of them before any expert compute starts. Reads overlap each other, which Research 1 already tested, but they never overlap compute. Within a layer, the reads consumed by the second and third expert nodes could stay in flight while the first node computes. That overlap needs no prediction. Across layers, nothing is causally known before a layer's router runs, so R5 does not issue reads across layers.

| file | content |
|---|---|
| [`PREREGISTRATION.md`](PREREGISTRATION.md) | scientific bridge from Research 2–4, question, hypotheses, falsifiers, design |
| [`RUNTIME_AUDIT.json`](RUNTIME_AUDIT.json) | 12 findings anchored in the runtime source at the source HEAD, capability matrix, prerequisites |
| [`COMPARATORS.json`](COMPARATORS.json) | A (baseline), C (deferred wait), B / C0_I / C_I (traced mechanism arms), why there is no D, oracle bound E |
| [`WORKLOADS.json`](WORKLOADS.json) | model, development and evaluation prompts (by hash), budgets, launch settings |
| [`ENDPOINTS.json`](ENDPOINTS.json) | primary endpoint, secondary endpoints, critical-path mechanism metrics, headroom bound H |
| [`INSTRUMENTATION.json`](INSTRUMENTATION.json) | instrumentation reused, instrumentation to add, what is not measurable and how it is handled |
| [`DECISION_RULES.json`](DECISION_RULES.json) | thresholds and their justification, precision plan, gates, blame rules, classification order, anti-rescue, HoPi gate |
| [`SCHEDULE.json`](SCHEDULE.json) | seeded, balanced block schedule for the three Phase 2 campaigns (196 counted launches) |
| [`PROVENANCE.json`](PROVENANCE.json) | every prior-evidence value used, read from its frozen artifact with its hash |
| [`CLAIM_BOUNDARIES.md`](CLAIM_BOUNDARIES.md) | what each outcome may and may not be called |
| [`REVIEW.md`](REVIEW.md) | the independent adversarial review and how each finding was resolved before freezing |
| [`FREEZE.json`](FREEZE.json) | file hashes, contract digest, freeze digest |
| `phase0.py`, `tests/` | contract builder and verifier; contract tests |

Verify:

    python shared/tooling/run_frozen_verifier.py experiments/R5-00-exposed-transfer-stall/phase0.py verify
