# R5 Phase 2 — prospective evaluation campaign (acquisition ready, not opened)

**State: `R5_PHASE2_ACQUISITION_READY`.** The evaluation is **LOCKED**: no evaluation prompt has been read, no Phase 2 launch has run, and no opening record exists. Opening it is a separate, explicit, one-shot step (`campaign.py authorize-opening`).

**Question (frozen).** Does the causal deferred-wait scheduler C lower end-to-end decode wall time per token by at least 2% relative to the baseline A at 1.5 GiB, by hiding expert-transfer wait behind compute, without harm at 4 GiB? The 95% lower bound must be ≥ 2%.

This step follows G1 (`R5_G1_HOST_CONFORMANCE_PASS`) and G2 (`R5_G2_HEADROOM_GATE_PASS`, H = 0.0844, an optimistic bound, never a gain).

**Schedule.** Read from the frozen `SCHEDULE.json`, never retyped:

| campaign | arms | blocks | counted launches | traced |
|---|---|---|---|---|
| `mechanism_1.5GiB` | B, C0_I, C_I (3 per block) | 12 | 36 | yes |
| `timing_1.5GiB` (primary) | A, C (ABBA / BAAB) | 20 | 80 | no |
| `timing_4GiB` (no-harm) | A, C (ABBA / BAAB) | 20 | 80 | no |

That is **196 counted launches**, plus one uncounted warmup per campaign (plus one after every resume), 60 s after a warmup and 10 s between launches.

| file | role |
|---|---|
| `PROTOCOL_2.json` | the pre-declared protocol: the frozen R5-00 texts quoted verbatim, the confirmed counts, the operational choices, the estimates |
| `campaign.py` | the host driver: `authorize-opening`, `run`, `status`, `analyze`, `verify`, `finalize` |
| `analysis.py` | the frozen statistics: G3–G7, INTEGRITY, the classification order |
| `mechanism.py` | the frozen mechanism metrics from traced launches, with strict per-arm telemetry checks |
| `monitor_phase2.ps1` | the PowerShell monitor: progress only, never a timing or label |
| `phase2.py`, `READINESS_2.json` | the readiness freeze and its verifier |
| `tests/` | synthetic-fixture tests |

**Gates** (DECISION_RULES; first matching row wins):

| row | condition | label |
|---|---|---|
| 1 | correctness failure of C, C_I or C0_I | `R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED` |
| 2 | campaign aborted, or A/B integrity failure | `R5_INVALID` |
| 3 | G7 purity fails | `R5_CONFOUNDED_NOT_PURE_SCHEDULING` |
| 4 | G3 + G4 + G5 + G6 | `R5_POSITIVE_RUNTIME_IMPROVEMENT` (the only improvement, and the only label that can unlock HoPi) |
| 5 | G3 + G4, not G5 + G6 | `R5_PRIMARY_GAIN_NOT_ROBUST` |
| 6 | G3, not G4 | `R5_RUNTIME_CHANGE_WITHOUT_MECHANISM` |
| 7 | G4, not G3 | `R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN` |
| 8 | neither G3 nor G4 | `R5_NO_REALISED_OVERLAP_NO_GAIN` |

**Integrity.**
- **Correctness failures end the campaign at once, and are never rerun.** For C, C_I and C0_I this means token digest, budget, pending reads, abort, or a wrong arm or budget echo. The label is `BLOCKED`.
- **A/B integrity failures also end it at once**, as `R5_INVALID`.
- **Saved results are never rerun.** The launch result is saved the moment the server stops. If post-processing is interrupted, the next run completes it from the saved result.
- **A slot is rerun at most once, and only if its failed attempt produced no outcome information** (no timings, no trace). The cause must be one of the four frozen ones and corroborated by the host: an unexpected-shutdown event, a Windows Update or Defender event, or the driver's own disk or AC-power stop. Otherwise the failure stands, and `finalize` attributes it.
- **A second failure of the same slot aborts the campaign.** `finalize` attributes any stop for which no cause is claimed: C, C_I or C0_I to `BLOCKED`, A or B to `R5_INVALID`.
- **One run at a time.** An exclusive run lock prevents concurrent runs. The monitor shows a live run's open attempt as running, never as failed.
- **Pause:** a `PAUSE` file in the output directory stops the run cleanly between launches. It is not a failure, and resuming starts with a warmup.
- **No partial analysis:** nothing is analysed before all 196 counted launches are valid. The analysis runs once, and every rerun and superseded launch is reported in the result.

**Disclosure.** G1's baseline fidelity used the reconstructed llama-research-v5 comparator. Phase 2 does not use v5.

**Superseded Phase 1b assertion.** The frozen Phase 1b test `test_no_phase2_artifact` asserted two things: that no evaluation-opening file exists, which still holds, and that no `experiments/R5-03*` directory exists. The second recorded the pre-G2 state and now fails by design. The test is hashed into the sealed Phase 1b readiness, so it is not edited.
