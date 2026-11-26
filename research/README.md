# Research index

Argine is one program in four closed research stages and one open one. This directory is the **navigation layer**: the frozen scientific files stay in `experiments/`, `paper/` and the shared directories (so frozen hashes and verifiers keep working); each folder here says which of those paths belong to which research, what was asked, how it ended and how to verify it. The machine-readable mapping is [`manifests/research-layout-map.json`](../manifests/research-layout-map.json).

| research | state | final result | index |
|---|---|---|---|
| Research 1 — observation and characterization | CLOSED | 27 experiments (13 supported, 7 falsified, 1 superseded, 6 inconclusive) | [research-1](research-1/README.md) |
| Research 2 — oracle headroom, causal prediction, predictive retention | CLOSED / FROZEN | `R2_12_FAIL` for real-runtime throughput at 4 GiB; replay-level gains | [research-2](research-2/README.md) |
| Research 3 — predictive retention in the real runtime | CLOSED / PUBLISHED | `R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME` | [research-3](research-3/README.md) |
| Research 4 — causal future-distance distillation | CLOSED | `R4_CFDC_CAUSAL_STATE_INSUFFICIENT` (`R4_CFDC_CLOSED`) | [research-4](research-4/README.md) |
| Research 5 — exposed transfer stall (latency hiding) | OPEN — G1 and G2 PASSED (sealed); evaluation LOCKED | none yet (`R5_G1_HOST_CONFORMANCE_PASS` and `R5_G2_HEADROOM_GATE_PASS` are gates, not results) | [research-5](research-5/README.md) |

Research 5 Phase 0 is frozen; its evaluation is locked. Verification commands: [`REPRODUCING.md`](../REPRODUCING.md).
