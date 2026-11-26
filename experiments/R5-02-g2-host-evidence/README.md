# R5 Phase 1b — canonical G2 headroom-gate evidence (sealed)

**Verdict: `R5_G2_HEADROOM_GATE_PASS`.** The pooled bound is H = 2072921725103767552 / 24558530549356953125 ≈ **0.0844074** of decode time. The frozen threshold is 1/25 = 0.04, inclusive, so H is about 2.11 × the threshold.

I verified the verdict three ways:
- the host result, computed once by the acquisition driver;
- the frozen estimator (`../R5-02-headroom-gate/headroom.py`), re-run on the 8 kept traces;
- a clean-room estimator (`independent/clean_room_h.py`), written by a separate verifier from the frozen texts only.

All three agree exactly, launch by launch (`REDERIVATION.json`).

**What H is.** H is an optimistic, loose **upper bound** on the expert-transfer wait that moving the wait could hide. It ignores interference, and its compute window spans to the layer's last expert node. It is an opening gate only. It is **not** an achieved, measured or achievable runtime improvement, and it is never reported as one (R5-00 `anti_rescue`). The PASS only permits Phase 2 to be prepared, and then opened by its own separate, explicitly authorized step. The evaluation stays locked.

| file | content |
|---|---|
| `host/` | the acquisition log, identity, result and launch logs, from `%USERPROFILE%\AppData\Local\Argine\r5\phase1b-headroom`, plus the host campaign marker. One transform only: the user-profile prefix becomes `%USERPROFILE%`, and CRLF becomes LF. |
| `MANIFEST.json` | every host file: size and raw SHA-256. The 8 kept gzip traces (386 MB) are hashed here and kept on the host, never committed. |
| `REDERIVATION.json` | per-launch integer sums of H_layer from the frozen and clean-room estimators, the trace identities, and the exact H |
| `G2_SEAL.json` | verdict, H, threshold, 14 acquisition checks, identities, interpretation, disclosures |
| `FREEZE.json`, `g2_seal.py` | the seal and its verifier: `python g2_seal.py verify [--host-dir DIR [--rederive]]` |

**Acquisition checks.** All 14 pass:
- the schedule is the pre-declared one (8 counted launches in order, plus 1 uncounted warmup that is excluded);
- every slot is arm B at 1.5 GiB, traced, and valid by the frozen driver;
- each slot has one attempt, with no rerun, no superseded trace and no unaccounted file;
- the log order is attempt, then trace received, then trace kept, then launch, and the trace hashes logged before the launch record agree;
- telemetry is valid on every launch (511 decode graphs);
- the runtime, model and store hashes match the freeze, checked after every launch;
- AC power was on before and after every launch;
- the run is bound to Phase 1b readiness `a0f773ab…` and G1 seal `3827b5a1…`;
- there is one token digest per prompt;
- H was computed once.

**Disclosures:**
- **Units.** H = Σ H_layer / Σ predicted_ms over the same 8 launches, as fixed in `PROTOCOL_1B.json` before acquisition. The literal ENDPOINTS wording (per decode token, as a fraction of mean predicted_ms / predicted_n) gives H × 512/511 ≈ 0.0845726. Both readings give the same classification. The second value is reported only because every H computed in R5 is reported; it is not used for any decision.
- **Monitor display.** The Phase 1b `status` command listed the slot still running under "failed:". This was display-only: the run decides slot states only at start-up, and the log has 9 attempts, 9 launch records, no rerun and no stop. The frozen Phase 1b files are unchanged; the Phase 2 monitor separates running and post-processing slots.
- **Tracing.** H is measured on traced launches. Tracing perturbs timing, so the denominator is traced decode time.
- **v5 comparator.** G1's baseline fidelity used the reconstructed v5 comparator, not the lost original. It does not enter H.
