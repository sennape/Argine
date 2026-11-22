# R2-12 runtime realization: v4 -- ARGINE_EXPERT_READ fail-closed fix + trace diagnostics

Additive to `RUNTIME-REALIZATION-PROVENANCE.md` (which documents v3). This
document covers only what changed between `llama-research-v3` and
`llama-research-v4`. v3 is left unchanged on disk and its provenance
document is not edited.

## Trigger

A real Windows-host run of `llama-research-v3`
(sha256 `ea72912f878340bc77e999a3870ace0caf94d532afc9afd14cee0b7296b37a7d`)
with `b_selected` (decay=0.99, interval=64), a 4 GiB expert-store budget,
completed 512 tokens of real inference (prompt tokens: 37, completion
tokens: 512, predicted_ms ~= 95830 ms, ~= 5.33 tokens/s). The startup line
`argine: retention policy attiva: b_selected (decay=0.99, interval=64)` was
printed. `ARGINE_RETENTION_TRACE=C:\modelli\conformance\diagnostic-b_selected-v3.jsonl`
was created but remained **0 bytes** after the complete run.

## Root cause

`servi_evento_con_politica()` in `patches/argine-store.cpp` is the sole
location of both the retention-policy decision dispatch for the cache's
single shard and the trace writer. It is reached **only** via
`psx_cache_ensure_many()`, which is called **only** when the read mode
(`ARGINE_EXPERT_READ`) is `blocco` (`LETTURA_BLOCCO`). The host run left
`ARGINE_EXPERT_READ` unset, so `argine-experts.cpp`'s default read mode,
`LETTURA_ASINCRONA`, served every real expert read via
`psx_cache_avvia()`/`psx_cache_attendi()` instead -- a code path with zero
awareness of the retention policy.

`psx_cache_attach_retention_policy()` still succeeded (hence the correct
"attiva" startup line and the correctly-`fopen()`-opened, 0-byte trace
file), but the policy was **never actually consulted for any real
eviction/admission decision during that run**, and the trace-writer code
(itself correctly written: conditioned per policy event rather than per
eviction, correct before/after causality via `inspect()`, and already
calling `fflush()`) was simply never executed, because its one call site
was never reached.

Confirmed two ways:
1. Static code-path analysis of `argine-experts.cpp`'s three-mode
   dispatch (`LETTURA_SINCRONA` / `LETTURA_BLOCCO` / `LETTURA_ASINCRONA`,
   default) and `Contesto::instance()`'s single `std::call_once` singleton
   (ruling out a separate cache/policy instance for inference).
2. The real host log
   `conformance/diagnostic-b_selected-v3.out.log`, which prints
   `... lettura asincrono` immediately after the "retention policy attiva"
   line -- i.e. the host's own startup transcript states the read mode
   that bypasses the policy.

Every alternative hypothesis raised was checked and ruled out: trace gated
on eviction only (false), event state returned before the trace call
(false), a different policy/cache instance serving inference (false,
single singleton confirmed), stream open/fail bits (fopen correctly
succeeds -- attach aborts entirely otherwise).

## Fix

`patches/argine-experts.cpp`, `Contesto::inizializza()`: a new fail-closed
`GGML_ABORT` immediately after the existing `ARGINE_EXPERT_SHARDS=1`
enforcement (same `if (psx_retention_policy_requested())` block, same
template), aborting at startup if a retention policy is requested while
the read mode is not `LETTURA_BLOCCO`. `ARGINE_EXPERT_READ=blocco` is now a
hard requirement for `ARGINE_RETENTION_POLICY` on this runtime -- a policy
that would otherwise be created but silently never consulted is now
refused outright instead of printing a misleading "attiva" line.

No change to B_selected, PredictedProtectPolicy, predictor behavior,
budgets, or eviction decisions.

## Diagnostics added

Independent of the JSONL trace file itself, per explicit request:

- `psx_retention_stats` (`patches/argine-store.h`) gains five fields:
  `trace_requested`, `trace_opened`, `trace_rows_attempted`,
  `trace_rows_written`, `trace_write_failed`.
- `psx_cache_attach_retention_policy()` prints
  `argine: ARGINE_RETENTION_TRACE: aperta con successo: <path>` to stderr
  at the moment `fopen()` succeeds.
- `servi_evento_con_politica()` now checks the `fflush()` return code and
  `ferror()` after every trace row; a failed write sets
  `trace_write_failed` and prints one (not per-row) stderr diagnostic
  naming the failing step.
- `Contesto::~Contesto()` prints an **unconditional** (not `-v`-gated)
  summary whenever a retention policy was active:
  `argine: retention trace diagnostica: traccia_richiesta=... traccia_aperta=... eventi_osservati=... righe_tentate=... righe_scritte=...`,
  with an explicit warning appended if events were observed but zero rows
  were written.

## Tests

`patches/argine-retention-integration-test.cpp`, new sections 7-10 (driven
against the real `Qwen_Qwen3-30B-A3B-Q4_K_M.psxs` store, through the same
`psx_cache_ensure_many()` entry point real inference uses):

7. a trace-enabled policy event produces a non-empty JSONL row containing
   real policy fields -- the exact symptom reproduced and fixed;
8. three events produce three distinct JSONL rows, not one cumulative row;
9. no `ARGINE_RETENTION_TRACE` set -> no file created, policy still served;
10. a forced write failure (`ARGINE_RETENTION_TRACE=/dev/full`, which opens
    successfully but fails every write with `ENOSPC`) is surfaced via
    `trace_write_failed` rather than silently swallowed by an unchecked
    `fflush()`.

All 10 sections (37 checks total, including the pre-existing sections 1-6)
pass:

```
g++ -O1 -g -std=c++17 argine-store.cpp argine-retention-policy.cpp \
    argine-research.cpp argine-retention-integration-test.cpp -o arxretest
./arxretest Qwen_Qwen3-30B-A3B-Q4_K_M.psxs
...
RISULTATO OK: 0 fallimenti
```

**Known test-coverage gap**: the new `ARGINE_EXPERT_READ` fail-closed guard
lives in `Contesto::inizializza()`, which requires a full ggml/llama.cpp
context to instantiate and is not reachable from this psx_cache-level
harness -- the same limitation as the pre-existing
`ARGINE_EXPERT_SHARDS=1` guard a few lines above it, which also has no
dedicated unit test. Its real coverage is this runtime rebuild and the
next host run's own startup behavior (the binary now refuses to start with
a retention policy requested and `ARGINE_EXPERT_READ` unset or anything
other than `blocco`).

## Commit

`f91c7f0` on `r2-main`: "R2-12: fail-closed on ARGINE_EXPERT_READ bypass of
retention policy + trace diagnostics". Touches only
`patches/argine-store.{h,cpp}`, `patches/argine-experts.cpp`, and
`patches/argine-retention-integration-test.cpp` -- nothing under
`experiments/`. Frozen R2-09 through R2-BRIDGE-01 evidence
(`contract.json`, `*-FREEZE.json`, `causal_boundary.json`,
`tournament_digest.json`) verified byte-identical before/after (sha256sum
over all such files in `experiments/`, diff clean).

## New runtime: llama-research-v4

Rebuilt incrementally from the same configured `build-win/` CMake tree used
for v3 (same pinned llama.cpp commit `254b177307b14d055bbe4c050c9c441a1e06d8a4`,
same user-owned MinGW-w64 GCC 10.3.0-posix toolchain, same
`-D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00` deviation): only
`ggml-cpu/argine-store.cpp` and `ggml-cpu/argine-experts.cpp` recompiled,
`ggml-cpu.a` relinked, all four binaries relinked against it.

Located at `C:\Users\alpel\Desktop\Argine2\llama-research-v4\` (outside
the git repo; `BUILD-INFO.json` mirrored into the repo at
`llama-research-v4/BUILD-INFO.json`, same convention as v2/v3).
`llama-research-v3` at `C:\Users\alpel\Desktop\Argine2\llama-research-v3\`
is untouched -- verified its `llama-server.exe` sha256 still matches
`ea72912f878340bc77e999a3870ace0caf94d532afc9afd14cee0b7296b37a7d` after
the v4 build.

| binary | sha256 |
|---|---|
| llama-server.exe | `b035d3913e2b88a58860fe4120f912c908f1fd7be37f8e4341089bfad2ad4d2e` |
| llama-imatrix.exe | `bb9496da8749972a1dedde310e9e6d98c0735cf9f2c0492e84cb597287f6b959` |
| llama-perplexity.exe | `a30608d6eb28c69497f9a9f89fe4d0c32651add46884f61e184d6b8ddb2b43d7` |
| llama-quantize.exe | `b44bf777e2692e6eb72b3486d6ff71ae692668f1cda7c5b738877befb45bf90b` |

Statically verified (`strings`, since it cannot run in this Linux
environment):
- all four new diagnostic format strings present in `llama-server.exe`:
  the trace-open confirmation, the destructor summary line, the
  `ARGINE_EXPERT_READ=blocco` abort message, and the write-failure
  message;
- the original `argine: retention policy attiva: ...` startup line is
  unchanged;
- `objdump` import-table DLL names unchanged per-binary from v3
  (`llama-server`/`imatrix`/`perplexity`: ADVAPI32, KERNEL32, SHELL32,
  WS2_32, msvcrt; `llama-quantize`: ADVAPI32, KERNEL32, msvcrt only) --
  same static-link, no-OpenMP build shape.

**Not run** in this environment (PE32+ Windows binary). No benchmark. No
warmups. `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` not created. Benchmark
remains 0/120.

## What the next host run should show

With `ARGINE_EXPERT_READ` left unset (the old default): the process now
**aborts at startup** with
`argine: ARGINE_RETENTION_POLICY richiede ARGINE_EXPERT_READ=blocco (o blocco:N): ...`
instead of starting and silently running an inert policy.

With `ARGINE_EXPERT_READ=blocco` set (the required setting going forward):
1. `argine: ARGINE_RETENTION_TRACE: aperta con successo: <path>` at
   startup, confirming the open.
2. `argine: retention policy attiva: b_selected (decay=0.99, interval=64) -- R2-12 runtime policy integration`,
   unchanged from v3.
3. The real request proceeds as before.
4. At shutdown:
   `argine: retention trace diagnostica: traccia_richiesta=si traccia_aperta=si eventi_osservati=<N> righe_tentate=<N> righe_scritte=<N>`
   with `<N>` matching across all three counters and > 0, and no
   "ATTENZIONE" warning appended.
5. The JSONL file at the `ARGINE_RETENTION_TRACE` path should now be
   non-empty, with one row per policy event.
