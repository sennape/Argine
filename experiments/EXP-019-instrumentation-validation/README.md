# EXP-019 — Research instrumentation: correctness and completeness

| field | value |
|---|---|
| ID | `EXP-019-instrumentation-validation` |
| Status | **SUPPORTED** |
| Period | 2026-10-03 |
| Origin | Argine2 |
| Related questions | RQ-011, RQ-012 |
| Supersedes / superseded | — |

## Research question

Does the Argine2 instrumentation (patch 0003) record every routing decision, slice access, read, eviction and node, without changing what the runtime does?

## Hypothesis

[HYPOTHESIS] Tracing is pure observation: identical hits/misses/evictions and identical model output with and without the trace; every miss has exactly one read record; the budget invariant holds.

## Setup

(1) Standalone C++ test `patches/argine-research-test.cpp` against a store built from the tiny MoE fixture, under ThreadSanitizer and ASan/UBSan; existing store tests (79 checks) and stress test re-run with tracing on. (2) End-to-end: patched llama-server built natively on Linux from the same sources (llama.cpp 254b177), tiny MoE fixture, store budget between one and two layers, two prompts x 40 tokens.

## Commands

```bat
g++ -fsanitize=thread argine-store.cpp argine-research.cpp argine-research-test.cpp -o arxtest && ./arxtest tiny.psxs 2 8 trace.raw
ARGINE_EXPERT_STORE=tiny.psxs ARGINE_EXPERT_BUDGET=275251 ARGINE_RESEARCH_TRACE=e2e.raw llama-server -m tiny.gguf
```

## Raw data

`raw/e2e-fixture/raw.tsv` (end-to-end raw log) and derived tables in `processed/e2e-fixture/`.

## Result

[MEASURED] All instrumentation checks pass under TSan and ASan/UBSan (stats identical with/without trace; 1:1 miss↔read; ordered timestamps; every eviction logged; 0 budget violations with 6 threads). End-to-end: 82 forward passes (2 prompts + warm-up), 164 router records, 3069 slice accesses, 71 reads (100% exact completions on the POSIX path), 38 evictions, 492 node records, 81 snapshots, 0 budget violations; runtime counters (71 misses) equal the trace. Generated text identical with no store, store, and store + trace.

[MEASURED, Windows smoke run 2026-10-03, not publication-quality: no git commit] Qwen3-30B-A3B Q4_K_M, 4 GiB budget, `coding` dev split (3 prompts × 128 tokens), binary `llama-research` ee463158: instrumentation confirmed by the runtime, 1,453,486 slice accesses, 144,508 reads, 0 failed reads, 0 malformed records, 0 budget violations; 66.6% of completions exact (the rest upper bounds), so queue depth is reported as an upper bound (time-weighted mean while I/O active ≤ 8.9, over the full span ≤ 4.8). [MEASURED] The trace lacked the last ~263 ms of the run: the final request has 126 of 127 decode steps plus one partial step (16 of 48 layers), although the file was well-formed — records buffered after the last 250 ms flush were lost when the process was terminated. Fixed in the harness (flush sentinel request + per-request completeness check), without rebuilding the binary.

## Interpretation

[DERIVED] The instrumentation is complete and observation-only on the tested paths. The Windows overlapped-I/O completion stamping (COMPLETE_EXACT via WaitForMultipleObjects) compiles but has NOT been executed: it runs only on Windows.

## Limitations

[LIMITATION] Tiny model; Linux POSIX read path; the Windows binary in llama-research/ is cross-compiled and untested at runtime. Overhead not measured (EXP-024).

## Status history

2026-10-03 SUPPORTED on the fixture. 2026-10-03 (later) Windows overlapped-I/O path executed in a smoke run (see Result); tail-loss defect found and fixed in the harness; complete publication-quality validation pending a rerun after `git init`.
