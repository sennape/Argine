# R3-04 instrumentation plan

## Finding (Part F)
* The R3-03B runtime already emits, at graceful exit, one `argine: policy_timing {...}` line (schema r3-03b-v1: `ns_total`, `ns_prediction_update`, `ns_victim_selection`, `victim_calls`, lock times, log2 histogram). That is per-arm policy time for A, S and P (Part G).
* In-memory hit / miss / eviction / bytes-read / resident counters exist (`psx_cache_stats`, `psx_retention_stats`) but the runtime does **not** export them in any non-verbose, non-trace mode (R3-00A root-cause finding; the R3-03B launch logs contain no such line). So there is **no** existing lightweight aggregate export.

## Decision: no runtime change
Adding an export line would need a new runtime identity, a rebuild and a new full conformance. It is **not needed**: retention-policy decisions are a deterministic function of the routing event stream, the stream is fixed by the greedy 512-token output (digest `ac28efbe...`, checked in every launch), and ARGINE_EXPERT_READ is blocking. The exact mechanism counters are therefore obtained once per (arm, budget) from a **dedicated trace-enabled mechanism phase**, completely separate from timing, and each timing launch is tied to it by its token digest plus `policy_timing.calls == 24,672` and (as an integrity check) `victim_calls` equal to the traced eviction-decision count of its arm/budget.
Trade-off stated honestly: the counters are measured in 10 traced launches, not in every timing launch; the equivalence rests on determinism (verified by the mechanism-phase OFF/ON check and the S-vs-A decision check), not on a per-launch counter.
Event-level tracing is **never** enabled in the timing campaign (it slows decode ~5x).

## Runtime identity and conformance (Parts L, M)
* Runtime: `llama-server.exe` SHA-256 `8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5` (R3-03B, llama.cpp `254b1773`, MSVC 19.44.35229). Unchanged; never overwritten.
* Existing conformance that stays valid: R3-03B (A and S at 8 GiB: 514 passes, 24,672 events, frozen digest, timing OFF/ON canonical traces identical, S == A decisions) and R3-03A semantic conformance (A 4 GiB, S 4 GiB, P 8 GiB on the earlier R3-03A runtime).
* Missing and therefore required **before** timing acquisition: full `predicted_protect` conformance on *this* binary at every R3-04 budget, and A/S at 1.5 and 4 GiB. This is the mechanism phase.

## Mechanism phase (frozen schedule in SCHEDULE.json, 10 launches, no timing claim)
| m | budget | arm | policy_timing |
|---|---|---|---|
| 1-3 | 1.5 GiB | A, S, P | ON |
| 4 | 1.5 GiB | P | OFF (observational check of the P victim path) |
| 5-7 | 4 GiB | A, S, P | ON |
| 8-10 | 8 GiB | A, S, P | ON |
Each launch: `ARGINE_RETENTION_TRACE` ON, same env otherwise, graceful shutdown. Offline per trace: (1) the frozen R3-03A streaming analyser (replay against the frozen R2 reference, protected-set semantics, no duplicate semantic events, no budget violation, 514 passes x 48 layers); (2) exact counters: decode miss bytes/token, hits, evictions, all-pass totals, resident peak (prefill = the first 2 passes; decode = the rest, divisor 512). Then the trace is reduced to a compact record (sha256 + size kept) and deleted by the driver.
Fail-closed gates (any one blocks timing acquisition): digest != frozen; events != 24,672; replay mismatch; S decisions != A decisions; P OFF vs ON canonical traces differ; budget violation; duplicate refusal. The *size or sign* of the retention improvement never gates anything.

## Policy-timing decomposition (Part G)
Per arm: `policy ms/token`; contrasts POL_COST (S-A), POL_RET (P-S), POL_NET (P-A). Wall retention effect net of bookkeeping: DELTA_RETENTION_ADJ.

## Disk (Part O)
Timing campaign < 6 MB. Mechanism: <= ~12 GB peak per trace (free-space gate 25 GB), one trace at a time, reduced and deleted; < 50 MB retained in total.
