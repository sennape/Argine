# Research 5 — preregistration

## 1. Scientific bridge

Research 2 established that expert routing in Qwen3-30B-A3B has predictive structure. A causal predictor beat trivial baselines and cut miss bytes in offline replay, but the real runtime showed no throughput gain at 4 GiB. Research 3 decomposed that failure. The predictor's own cost explained most of the runtime loss. Prediction-led retention stayed net-negative at 1.5 GiB, and the best causal next-use policy recovered only a small share of the clairvoyant miss-byte headroom. Research 4 asked whether richer causal state could recover materially more of that headroom. Within its frozen feature vocabulary and model families, none of the tested causal policies reached the preregistered 25% level. These results do not show that retention or learned eviction is impossible in general. They show that, in this regime, the tested approaches to *avoiding* expert transfers do not recover enough at low enough cost to improve the real system.

Research 5 therefore changes the target. It no longer asks which expert should remain resident. It accepts that some transfers are unavoidable and asks how much of their latency can be kept off the token critical path. The runtime already performs compute during which a transfer could proceed.

## 2. What the runtime allows

Inspection of the expert-store hook (`RUNTIME_AUDIT.json`) gives four findings:

- The experts of layer *L* are known exactly only once the router of layer *L* has run.
- At the first expert node of the layer, reads for all three weight kinds are issued as overlapped I/O.
- Each thread then waits for all of its reads before any expert compute.
- The reads consumed by the second and third expert node could stay in flight while the first node computes. This uses the same information the baseline already uses: no prediction, no new thread, no extra residency.

Earlier issue, across layers, would require predicting expert ids. That is Research 2–4 territory and is excluded. There is therefore one causal intervention and no "stronger" causal scheduler to add.

## 3. Question and hypotheses

The frozen question and hypotheses are in `PROTOCOL.json`. In short:

- **Primary.** At 1.5 GiB, the deferred-wait arm C lowers decode wall ms/token relative to the current runtime A. The 95% lower bound of the mean block relative reduction must be at least 2.0%.
- **Null.** The reduction is below 2.0%.
- **Mechanism.** C lowers the critical-path wait inside the store hook relative to a bookkeeping-matched control C0. At least 25% of deferred bytes arrive before they are needed. Wait reduction explains at least half of the reduction in hook time.
- **Headroom (opening gate).** On development prompts only, a one-shot trace-derived optimistic bound H is at least 4% of decode time.
- **No harm.** At 4 GiB, C is not slower than A by more than 2%.

## 4. Design

**Model and settings.** Qwen3-30B-A3B-Q4_K_M with the expert store, `asincrono` LRU read path, 12 threads, context 4096, 512 greedy decode tokens with `ignore_eos`, one fresh process per launch.

**Arms.** All arms use one binary, frozen at the end of Phase 1a:
- A: the baseline.
- C: the deferred-wait arm.
- B: A with tracing on.
- C0_I: C's bookkeeping with A's wait placement, traced.
- C_I: C with tracing on.

Traced arms are compared only with each other. The timing campaign is untraced.

**Prompts.** Development uses `code-dev-1`, `nl-dev-1`, `math-dev-1` and `json-dev-1`. Evaluation uses `code-eval-1`, `nl-eval-1`, `math-eval-1` and `json-eval-1`, which no binary may run before the evaluation opens.

**Campaigns.** The order of campaigns, blocks and arms is seeded and frozen in `SCHEDULE.json`:
- mechanism, 1.5 GiB: 12 three-launch blocks;
- primary timing, 1.5 GiB: 20 palindromic four-launch blocks;
- no-harm timing, 4 GiB: 20 palindromic four-launch blocks.

**Phases.**
- Phase 1a: implementation and conformance (gate G1), then the implementation freeze.
- Phase 1b: the one-shot headroom gate G2.
- Phase 2: the evaluation, opened once.

## 5. Falsification

The direction is falsified, and is not reinterpreted as success, if any of the following holds:

- H is below 4%.
- The deferred wait cannot be implemented correctly.
- Exposed wait does not fall against C0, or too few deferred bytes arrive in time.
- The wait falls but end-to-end decode does not improve by 2%.
- Residency changes by more than 1%.
- The gain does not survive the 4 GiB no-harm check or the cross-prompt consistency check.

The classification order in `DECISION_RULES.json` applies mechanically. Only `R5_POSITIVE_RUNTIME_IMPROVEMENT` is an improvement, and only that outcome can lift the HoPi pause.
