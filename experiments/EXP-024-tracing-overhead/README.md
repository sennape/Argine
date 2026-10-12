# EXP-024 — Cost of the research instrumentation

| field | value |
|---|---|
| ID | `EXP-024-tracing-overhead` |
| Status | **PLANNED** |
| Period | — |
| Origin | Argine2 |
| Related questions | RQ-012 |
| Supersedes / superseded | — |

> **Protocol status (2026-10-12).** Prospective protocol EXP024-P1, frozen before any measurement, with its hash in [`protocol_freeze.json`](protocol_freeze.json). Independent review completed 2026-10-12; no measurement has yet been performed. The pre-review candidate freeze is kept byte-for-byte as [`protocol_freeze_pre_review_2026-10-12.json`](protocol_freeze_pre_review_2026-10-12.json); it is history and was never executed.
>
> **No measurement has been made under this protocol.** The status stays PLANNED until the first real launch, as with EXP-010. It becomes RUNNING when the session starts, and receives a verdict only through the frozen rule below.

## Research question

How much does enabling `ARGINE_RESEARCH_TRACE` slow generation on the reference Windows machine?

## Hypothesis

[HYPOTHESIS] Under 2% at every budget. *(Written 2026-10-03, before any measurement; the 2% threshold is frozen and will not change after data exist.)*

<!-- EXP-024 PROTOCOL-BEGIN -->
## Setup

### What is compared

This is research instrumentation **ENABLED** (`ARGINE_RESEARCH_TRACE` set) versus **DISABLED** (variable absent), on the **same** `llama-research-v2` binary. It is not patched vs unpatched.

- **Tracing OFF.** The compiled hooks remain in the binary. After initialisation each hook reaches `arx_enabled()`, whose current fast path performs one atomic state load and returns. The header comment still calls that load "relaxed", while the current source uses `memory_order_acquire`; this documentation mismatch is not itself an EXP-024 measurement.
- **Tracing ON.** The full enabled research path runs: record formatting, per-thread buffers and file writes. On Windows it also runs the precise completion wait, `arx_wait_precise` (`WaitForMultipleObjects` one completion at a time), which is used only while tracing.
- **What the estimate is.** EXP-024 estimates the incremental cost of that whole enabled path relative to the same binary with tracing disabled. It is not file-I/O or logging overhead alone, and the components are not decomposed.

### Frozen configuration (identical for OFF and ON)

| item | value |
|---|---|
| model | `C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf` (Qwen3-30B-A3B Q4_K_M) |
| Expert Store | `C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs`, resolved by `--expert-store` and verified in every launch log |
| runtime | `llama-research-v2` (`ARGINE_LLAMA_DIR`), instrumentation `argine2-research-v2` |
| backend | `--backend cpu`. Effective CPU: BUILD-INFO enables no Vulkan backend and the binary links no `vulkan-1.dll` |
| read mode | `asincrono` |
| generation threads | 4 (`--threads 4`) |
| prompt / batch threads | 12, as resolved by the benchmark planner on the reference i5-1340P (verified, not overridden) |
| expert budgets | 4G and 1.5G |
| workload | `argine.bench.DEFAULT_PROMPT` (304 chars, SHA-256 `b1677835fc599087f01f08bf9ab3a07f088351e443740209de52be40778095f3`), normal benchmark warm-up, `--n-predict 128`, `--repeat 1` |
| other flags | `--retries 0`, `--expert-stats` (same in both conditions) |

**Why threads = 4.** EXP-024 measures how much the enabled instrumentation perturbs the principal instrumented research path. That path ran with threads 4 / threads-batch 12 in EXP-019, EXP-021 and the trace campaigns. The goal is not the fastest thread setting. EXP-027 found 12 threads faster for the 30B at 4 GiB, but it did not test 1.5 GiB, so its setting is not substituted here.

### Replication unit

One independent replicate is one complete `argine benchmark` launch:
- a fresh llama-server process and a fresh (empty) Expert Store;
- the normal benchmark warm-up;
- exactly one measured request (`--repeat 1`, `--n-predict 128`, `--retries 0`).

Several requests inside one server process never count as several replicates.

### Execution order (frozen, 20 launches)

| round | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| 1 | 4G-OFF | 4G-ON | 1.5G-OFF | 1.5G-ON |
| 2 | 1.5G-ON | 1.5G-OFF | 4G-ON | 4G-OFF |
| 3 | 4G-ON | 4G-OFF | 1.5G-ON | 1.5G-OFF |
| 4 | 1.5G-OFF | 1.5G-ON | 4G-OFF | 4G-ON |
| 5 | 4G-OFF | 4G-ON | 1.5G-OFF | 1.5G-ON |

- The two conditions of a budget are adjacent in every round, while which condition comes first and which budget comes first both vary.
- Over the session OFF comes first 3 times per budget and ON 2 times; 4G opens 3 rounds and 1.5G 2.
- The order is hard-coded in `tools/exp024_run.py` and checked. `stats.interleaved_schedule(["OFF","ON"], 5)` is deliberately not used: with two configurations it yields OFF ON OFF ON …, i.e. OFF first every time.
- Cooldown: 60 s between complete launches, none before launch 1.
- The schedule, the command line and the environment change of every launch are written to `raw/session.json` before launch 1.

### How ON and OFF are set and verified

The factor is set only in the environment of the `python -m argine benchmark` child process. `LlamaServerProcess` passes that environment on to llama-server, and the benchmark command line is byte-identical in both conditions.

The tool refuses to start if the parent environment holds `ARGINE_RESEARCH_TRACE` or any other `ARGINE_*` / `PICOSTAR_*` variable except `ARGINE_LLAMA_DIR` / `ARGINE_HOME`.

- **ON:**
  - the tool sets `ARGINE_RESEARCH_TRACE` to a fresh file, `raw/traces/launch-NN-rR-<budget>-ON.tsv`;
  - the launch log must contain exactly one `argine: research trace attiva: <that path>` line (the instrumentation's own stderr confirmation, echoed by `argine benchmark`) and no "cannot open" line;
  - the file must exist, be non-empty and start with the `H argine-raw 2` header;
  - it must be the only new file in `raw/traces/`.
- **OFF:**
  - the variable is absent;
  - the log must contain no `research trace` text;
  - the launch's would-be trace file must not exist, and `raw/traces/` must gain no file.

### Per-launch validation (any failure stops the session; no retry)

Every launch must have:
- exit status 0, and exactly one new `benchmark` record in the shared run log with no other events;
- the expected model path and size, and a runtime build carrying the binary SHA prefix;
- plan threads 4, threads_batch 12, backend cpu (plan and runtime), gpu_layers 0;
- the Expert Store on, with the expected budget in bytes;
- the store log showing the expected `.psxs` opened with `lettura asincrono`, `IN USO`, and no Vulkan device handling;
- the store-reported read mode `asincrono`;
- `repeat` 1 with exactly one measured run, `n_predict` 128 and `prompt_chars` 304;
- no benchmark errors, no start-up retries (`attempts` empty), and generation tok/s present;
- the ON/OFF evidence above.

A failed launch is never retried: a retry would not be the preregistered launch. The session is then recorded as INVALID, and a new session needs a clean `raw/` and a documented deviation.

### Provenance (before launch 1; completed after launch 20)

Before launch 1:
- Argine2 commit and dirty state;
- full SHA-256 of `llama-server` (checked against BUILD-INFO's declared hash) and of `BUILD-INFO.json`;
- the instrumentation marker;
- the model and store snapshots: canonical path, size, mtime and the Argine2 fast fingerprint, plus a full SHA-256 only if already known without reading the file (the EXP-027 large-file policy). Neither file is fully read before or between launches;
- CPU and OS, best-effort power scheme / AC state, free RAM (session and per launch);
- the frozen protocol and the freeze hashes;
- the schedule, prompt text and hash, `n_predict`, cooldown and every command.

After launch 20:
- missing full hashes are computed;
- the session is valid only if model, store, binary and BUILD-INFO are unchanged.

The shared Argine run log is never rewritten: each record is copied into `raw/records.jsonl` with an `exp024` block that references its source offset.

**Real-run gate.** `--run` starts only if all of the following hold:
- Git is available and HEAD resolves to a commit;
- the five protocol files (this README, `config.json`, `protocol_freeze.json`, `tools/exp024_run.py`, `tests/test_exp024.py`) are tracked;
- `git status --porcelain` is empty, untracked files included;
- the freeze matches.

The gate runs before any subprocess, session file or trace, and again just before `raw/session.json` is written. The checked HEAD is recorded in the session. `--dry-run` and `--preflight` do not apply the gate.

### Metrics and analysis

- **Primary metric: generation throughput `gen_tok_s`.** Per round and budget,

  `overhead_pct = 100 × (tok_s_OFF − tok_s_ON) / tok_s_OFF`

  A positive value means tracing is slower; a negative value means tracing happened to be faster.
- **Paired observations.** The five round-level differences are the paired observations of a budget.
- **Reported for each budget:**
  - the five OFF values, the five ON values and the five paired `overhead_pct`;
  - mean, median, sample SD and CV of OFF and ON throughput;
  - the mean paired `overhead_pct` with its two-sided 95% Student-t confidence interval (df = 4, t = 2.776445).
- Ratios of pooled, unpaired repetitions are not used for the primary result.
- **Secondary (descriptive):** the paired ms/token change with its CI. Median TTFT, prompt tok/s, RAM peak, CPU mean, store hit percentage and the effective read mode come from the benchmark record. They are never promoted to primary hypotheses.
- **Trace files** are kept; no replay analysis is required for this question.

### Decision rule (frozen)

The hypothesis is overhead < 2% at every tested budget.
- **SUPPORTED:** the upper bound of the 95% CI of the mean paired overhead is < 2% at both 4G and 1.5G.
- **FALSIFIED:** the lower bound of that CI is > 2% at at least one budget.
- **INCONCLUSIVE:** otherwise, including a CI that crosses or touches 2% at either budget.

The raw effects and CIs are reported whatever the verdict. No other threshold may be introduced after the data exist. An incomplete or invalid session yields no decision.
<!-- EXP-024 PROTOCOL-END -->

## Commands

```bat
python tools\exp024_run.py --dry-run      :: schedule, commands, env changes; no subprocess
python tools\exp024_run.py --preflight    :: static checks + `argine benchmark --dry-run` (GGUF header only, no model load)
python tools\exp024_run.py --run          :: the 20 launches; refuses unless the protocol files are committed and the tree is clean
python tools\exp024_run.py --analyze      :: results.csv + processed/summary.json
python -m unittest discover -s tests -p "test_exp024.py" -v
```

## Raw data

Pending. The planned contents of `raw/`:
- `session.json`: provenance, frozen protocol, schedule and commands;
- `records.jsonl`: one benchmark record per launch, copied unchanged, with an `exp024` block;
- `logs/launch-NN.log`;
- `traces/launch-NN-rR-<budget>-ON.tsv` (ON launches only).

## Result

No result yet: no launch has been executed under protocol EXP024-P1.
- Not a measurement: the earlier fixture smoke test (2.6 vs 2.6 tok/s on an oversubscribed 2-core container).
- Not an OFF control: EXP-019's 6.1166291 tok/s, which was a traced run.

## Interpretation

—

## Limitations

- [LIMITATION] **Scope.** One machine (i5-1340P, Windows 11), one binary (`llama-research-v2@a0063720`), the 30B Q4_K_M with the asynchronous Expert Store, threads 4 / batch 12, the standard benchmark workload (77 prompt tokens, 128 generated), and budgets 4G and 1.5G. No claim is made for other models, hardware, thread settings, budgets or workloads.
- [LIMITATION] **What is measured.** The enabled-vs-disabled cost on the instrumented binary. The cost of the compiled-in disabled hooks relative to an unpatched binary is not measured.
- [LIMITATION] **Not decomposed.** The ON cost includes record writing and, on Windows, the precise completion wait. Their shares are not separated.
- [LIMITATION] **Trace output location.** ON launches write a trace file (tens of MB) to `raw/traces/`, on the same machine's disk. That I/O is part of the measured overhead, as it is in real trace runs.
- [LIMITATION] **Sample size and order.** Five paired rounds per budget (df = 4) give wide intervals if launch-to-launch noise is large, so INCONCLUSIVE is a plausible outcome. Order is balanced, not randomized: OFF comes first 3 times and ON 2 times per budget.
- [LIMITATION] **Single-request replicate.** One measured request of 128 tokens per launch makes each replicate sensitive to start-up and cache state. The fresh process and empty store make this identical for both conditions.

## Status history

- 2026-10-03 PLANNED.
- 2026-10-12 PLANNED, protocol EXP024-P1 frozen with the harness `tools/exp024_run.py` and tests `tests/test_exp024.py`; pending independent review. No measurement.
- 2026-10-12 Independent review completed before any measurement. The first candidate freeze (`protocol_freeze_pre_review_2026-10-12.json`) was superseded by the reviewed freeze after two fixes:
  - the atomic-load wording in the protocol;
  - a hard committed/clean Git gate for `--run`.

  The design is unchanged. Status PLANNED; no measurement.
