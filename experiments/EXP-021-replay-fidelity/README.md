# EXP-021 — Does replay reproduce the runtime?

| field | value |
|---|---|
| ID | `EXP-021-replay-fidelity` |
| Status | **SUPPORTED** |
| Period | 2026-10-03 → 2026-10-04 |
| Origin | Argine2 |
| Related questions | RQ-011, RQ-010, RQ-018, RQ-005 |
| Supersedes / superseded | supersedes the `sharded-lru-16` "runtime model" rows of EXP-009 and the pilot heterogeneity numbers of EXP-008/EXP-010 (see below) |

Full write-up: [`docs/research/REPLAY-FIDELITY-VALIDATION.md`](../../docs/research/REPLAY-FIDELITY-VALIDATION.md).

## Research question

Given the router trace recorded by the runtime, does replaying it through a model of the runtime's policy reproduce the runtime's decisions: every hit or miss, and every eviction victim?

## Hypothesis

- H1 (original): [FALSIFIED as a runtime reproduction] replay with `sharded-lru-N` at expert granularity predicts the runtime within 10%. On the 30B it is 3.5–4.8 pp too high on the runtime's own metric. At matched metric it is still 1.5–2.1 pp too high. With small shards it is off by up to 12 pp.
- H2: [SUPPORTED on Linux; FALSIFIED on the first Windows v2 run, 5.29% disagreement] a replay of the store's slice-level, per-shard, pin-aware LRU, fed the runtime's own request order, reproduces every decision when the shard map is known. When the shard map is modelled statistically, it reproduces the aggregate within 0.3 pp.
- H2′ (2026-10-04): [SUPPORTED, confirmed out of sample] the same replay with **per-thread** pins reproduces every decision. Each thread drops its own pins when it enters a node of the pool computing that graph; llama.cpp computes prompt and decode graphs on different threads when `--threads ≠ --threads-batch`.

## Setup

- (a) Real `argine-store.cpp` driven by a deterministic C++ driver: 1–4 shards, unequal slice sizes, prompt steps.
- (b) Native Linux end-to-end runs with the format-v2 instrumentation: mid-size MoE fixture, 6 × 32 experts, 16 shards, 24 MiB and 14 MiB. The shard map is recorded.
- (c) The nine Windows EXP-007 runs (30B, coding dev, 4G/2G/1.5G × 3, format v1), with the shard map modelled by the runtime hash and 3 seeds.

Validator: `argine/research/fidelity.py`. Replay: `argine/research/storereplay.py`.

## Commands

```bat
argine research fidelity experiments\EXP-007-router-characterization\raw\30b-coding-dev --out experiments\EXP-021-replay-fidelity\processed\30b-coding-dev
argine research fidelity experiments\EXP-021-replay-fidelity\raw\linux-mid-moe-v2 --out experiments\EXP-021-replay-fidelity\processed\linux-mid-moe-v2
python -m unittest tests.test_fidelity
```

## Raw data

- (b) `raw/linux-mid-moe-v2/run-00-24M`, `run-01-14M`. Full run directories: `raw.tsv` v2, manifest, summary.
- (c) The EXP-007 raw runs, unchanged.
- (d) `raw/30b-coding-dev-v2/run-00-4G`: the first Windows format-v2 run (2026-10-04), recorded on the reference machine with `llama-research-v2`. Its validation as originally produced is kept in `processed/30b-coding-dev-v2/` (FAILED, see `FAILED-VALIDATION.md` there).

## Result

[MEASURED] (a) and (b): **0 disagreements**.
- Linux 24 MiB: 0 / 89,892 requests; 1,171 misses, 794 evictions, identical bytes.
- Linux 14 MiB: 0 / 89,928; 2,563 misses, 2,339 evictions.
- 0 skipped victims (pins never reached the LRU tail).

[DERIVED] (c) runtime-reproduction replay vs runtime, gate-slice hit, all phases, workload:

| budget | runtime | store-replay | Δ | old `sharded-lru-16` | old Δ | event disagreement |
|---|---|---|---|---|---|---|
| 4 GiB | 70.60% (3 runs) | 70.72% | +0.10…+0.14 pp | 75.32% | +4.7 pp | 0.68–0.79% |
| 2 GiB | 49.75% | 49.91% | +0.16…+0.17 pp | 53.30% | +3.6 pp | 0.73–0.75% |
| 1.5 GiB | 41.46% | 41.66% | +0.11…+0.28 pp | 45.05% | +3.6 pp | 0.75–0.78% |

[MEASURED] Control: the Linux v2 runs replayed with the hash model instead of the recorded map show a disagreement of 0.64–0.75% (24 MiB). This is the size seen on the Windows runs. Per-run table: `processed/30b-coding-dev/fidelity_runs.csv`.

[DERIVED] Optimal-static anomaly. The old DP charged 2,654,208-byte experts as 3 MiB, a 512 KiB grid, which made the uniform split infeasible. On the same v1-legacy curves the exact DP gives 34,091 misses vs uniform 36,473; the old DP gave 38,206.

[MEASURED, 2026-10-04] (d), the first Windows v2 run, gave 5.29% event disagreement and 3 shards with sequence gaps; closure FAILED.
- **The divergence** came from the replay's single-pool pin model. The run used `--threads 4 --threads-batch 12`, so idle-pool threads keep their pins, and 78,394 of 144,689 evictions passed over pinned slots.
- **The gaps** are 5 hits lost from the file buffer at process termination, inside the flush sentinel: replay-irrelevant.

[DERIVED] With per-thread pins the same trace replays with **0 of 1,474,909** decisions different:
- identical skip counts on all 78,394 evictions that passed over pinned slots;
- identical misses, miss bytes and evictions;
- `shards_with_replay_relevant_seq_gaps = 0`, incomplete workload nodes 0.

The old model rerun reproduces 0.052928011 exactly. On the nine v1 runs, the per-thread model removes the +0.1…+0.3 pp optimism (now −0.07…+0.06 pp). §13 of the write-up has the details.

[MEASURED, 2026-10-04] Fresh 30B confirmation run (`raw/30b-coding-dev-v2-confirm`): 0 of 1,474,817 decisions differ. Misses, miss bytes and evictions are identical, and the 71,863 evictions past pinned slots carry identical skip counts. Closure PASS.

[MEASURED, 2026-10-04] 80B coding-dev campaign (EXP-007, 9 runs): 7 runs exact over the full trace. In run-04-1.5G and run-07-4G, 2 and 4 eviction victims differ, all in the flush sentinel after the last workload request. Each lies in the one incomplete node, in a shard that had lost records at process termination. Over the workload both runs are exact.

The validator now reports three verdicts:
- `full_trace_fidelity` (strict, diagnostic);
- `workload_fidelity`;
- `termination_tail_completeness`.

Closure requires workload fidelity and no unexplained tail disagreement. See write-up §14.

## Interpretation

[SUPPORTED] The 3.5–4.8 pp gap was mostly a **metric** mismatch. The runtime counts gate-node slice hits over prefill + decode; the replay counted expert hits over decode only. Two smaller **replay** differences add to it:
- prefill was not served to the state;
- slice granularity and non-atomicity, with 3% partially resident expert events at 4 GiB, plus shard mapping.

It was not a runtime bug and not an instrumentation bug. Format-v1 records lack the shard id, which limits exactness to statistical. Format v2 adds it.

## Limitations

- [LIMITATION] (resolved 2026-10-04) The per-thread pin model was corrected on the same Windows trace; the fresh confirmation run passed.
- [LIMITATION] Records produced after the last flush are lost when llama-server is terminated. The flush sentinel absorbs this tail; the validator verifies on every run that no lost record precedes a workload record.
- [LIMITATION] Thread pools are told apart by thread count (`nth`): two pools of equal size would be merged. The validator would then report disagreements.
- [LIMITATION] Synthetic streams (no thread information) model each pool as one thread: 1.25% event disagreement and −0.01 pp on the Windows v2 trace.

- [LIMITATION] The 30B Windows equivalence is statistical: v1 traces lack the address-dependent shard map. One short v2 trace makes it exact (command in the write-up, §12).
- [LIMITATION] The interleaving is replayed as recorded, not simulated.
- [LIMITATION] The `w` (wait) and `U` (undo) paths are not exercised by EXP-007.

## Status history

- 2026-10-03 RUNNING (pilot, fixture).
- 2026-10-03 SUPPORTED: event-level equivalence on v2 traces; all nine 30B runs within 0.3 pp. Windows v2 confirmation trace pending.
- 2026-10-04 back to RUNNING. The first Windows v2 run failed closure (5.29%: per-thread pins with two thread pools; 3 replay-irrelevant sequence gaps). With the corrected replay that trace gives 0 disagreements. One fresh Windows run is pending.
- 2026-10-04 SUPPORTED: the 30B confirmation run passes. The 80B campaign is exact over the workload of all 9 runs; its 6 tail disagreements are explained by termination record loss. Closure semantics split into full-trace, workload and termination-tail verdicts.
