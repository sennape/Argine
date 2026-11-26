# Research 5 — exposed transfer stall (ETS)

**State: OPEN, Phase 1a IMPLEMENTED** (`R5_ETS_PHASE1A_IMPLEMENTED`; protocol `R5_ETS_PHASE0_FROZEN`). Host conformance (G1): `R5_G1_HOST_CONFORMANCE_PASS`, sealed in [`R5-01-g1-host-evidence`](../../experiments/R5-01-g1-host-evidence/README.md). Evaluation: LOCKED. No runtime result exists.

**Question.** In the expert-store runtime (Qwen3-30B-A3B-Q4_K_M, asynchronous LRU read path, 1.5 GiB), can a causal deferred-wait scheduler lower decode wall time per token by at least 2%? The scheduler waits for each expert-weight kind only at the node that consumes it, instead of waiting for all kinds of the layer before any expert compute. It adds no prediction and changes no residency decision. The aim is to keep unavoidable transfer latency off the decode critical path.

**Why this direction.** Research 2–4 tried to *avoid* expert transfers through prediction and causal retention, and within their tested regimes none produced a real-runtime gain. Research 5 targets the *exposed latency* of transfers that cannot be avoided. The runtime audit found one causal overlap opportunity: inside each layer, after the router decision, reads for the second and third expert node can stay in flight while the first node computes. Across layers, overlap would require prediction and is excluded.

| phase | directory | state |
|---|---|---|
| Phase 0 — protocol freeze | [`R5-00-exposed-transfer-stall`](../../experiments/R5-00-exposed-transfer-stall/README.md) | frozen after independent adversarial review ([`REVIEW.md`](../../experiments/R5-00-exposed-transfer-stall/REVIEW.md)) |
| Phase 1a — implementation and conformance (development prompts) | [`R5-01-ets-implementation`](../../experiments/R5-01-ets-implementation/README.md) | implemented; model-free implementation gate PASS ([`GATE.json`](../../experiments/R5-01-ets-implementation/GATE.json)); host part of G1 PASS, 17/17 criteria and 42/42 launches, sealed ([`R5-01-g1-host-evidence`](../../experiments/R5-01-g1-host-evidence/README.md); the v5 comparator is a disclosed reconstruction); runtime sources [`patches/r5-ets`](../../patches/r5-ets/README.md), build [`llama-research-r5-ets`](../../llama-research-r5-ets/BUILD-INFO.json) |
| Phase 1b — one-shot headroom gate | — | not started |
| Phase 2 — evaluation (opened once) | — | locked |

**Outcome labels.** These are frozen in [`DECISION_RULES.json`](../../experiments/R5-00-exposed-transfer-stall/DECISION_RULES.json). Only `R5_POSITIVE_RUNTIME_IMPROVEMENT` is an improvement, and only it can lift the HoPi pause. The other labels are:
- `R5_PRIMARY_GAIN_NOT_ROBUST`
- `R5_RUNTIME_CHANGE_WITHOUT_MECHANISM`
- `R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN`
- `R5_NO_REALISED_OVERLAP_NO_GAIN`
- `R5_NO_MEANINGFUL_OVERLAP_HEADROOM`
- `R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED`
- `R5_CONFOUNDED_NOT_PURE_SCHEDULING`
- `R5_INVALID`

**HoPi gate.** HoPi stays paused. No HoPi M9+ work is authorized until Argine demonstrates a prospectively validated real-runtime improvement.

## Verification

```
python shared/tooling/run_frozen_verifier.py experiments/R5-00-exposed-transfer-stall/phase0.py verify
python shared/tooling/run_frozen_verifier.py experiments/R5-01-ets-implementation/phase1a.py verify
python shared/tooling/run_frozen_verifier.py experiments/R5-01-g1-host-evidence/g1_seal.py verify
```
