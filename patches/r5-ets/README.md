# Research 5 (ETS) runtime sources

The sources of the Research 5 runtime. This tree is **additive**: the frozen Research 1–4 runtime sources in `patches/` stay byte-identical. They are hash-locked by the consolidation manifest. This directory holds full R5 copies of the five files that change, plus the new files listed below:

| file | status |
|---|---|
| `argine-store.{h,cpp}` | R5 copy: adds `psx_cache_attendi_kinds`, `psx_cache_pending_reads`, `psx_cache_pending_done` and a POSIX test-only deferred-read backend. `psx_cache_attendi` and the Windows read path are unchanged. |
| `argine-experts.cpp` | R5 copy: adds the fail-closed `ARGINE_EXPERT_WAIT` arm selection, the arm identity line, the per-thread `T` trace record and the end-of-run `r5_counters` line. The `layer` baseline path is unchanged. |
| `argine-research.{h,cpp}` | R5 copy: adds the `T` record (`arx_thread_node`). |
| `argine-ets.h` | new: the deferred-wait scheduler (`layer_touch`, `kind`). It is header-only and ggml-free, so the tests run the exact code the hook runs. |
| `argine-ets-test.cpp` | new: model-free conformance tests (POSIX deterministic backend, or Windows OVERLAPPED). Group 11 is the regression test of the host heap-corruption incident. |
| `argine-tls.h` | new: `argine_thread_object<T, Tag>()`, the per-thread state of the store, the trace and the hook. With mingw-w64 GCC 10 a `thread_local` destructor ran on storage emulated TLS had already freed (heap corruption at thread exit); on mingw the objects now live behind a pthread key and are destroyed on live storage. Elsewhere it is the plain `thread_local`. |
| `mingw-tls-emulation.cpp` | new, tests only: emulates the mingw thread_local teardown on Linux (with `clang++ -femulated-tls`), so ASan reproduces the incident on the old sources and checks the fix. |
| `R5_CHANGES.diff` | the complete diff of the R5 copies against the frozen files in `patches/`. |
| `build-r5-ets.sh`, `toolchain-mingw-w64.cmake`, `mingw-toolchain-debs.sha256`, `pe_normalized_sha256.py` | reproducible cross-build, identical in lineage to llama-research-v3..v5. |

The per-thread objects (`StatoThread`, the trace `ThreadBuf`, the hook's pin list, ETS state and routing buffer) are reached through `argine_thread_object`; this changes where they are stored, not what they hold or when they are created and destroyed.

Every other runtime file (`argine-experts.h`, `argine-retention-policy.{h,cpp}`, patches 0001–0006) is taken unchanged from `patches/`.

## Arms (`ARGINE_EXPERT_WAIT`)

| value | arm | what changes |
|---|---|---|
| unset or `layer` | A, the baseline | Nothing. This is the existing asincrono path. |
| `layer_touch` | C0, the bookkeeping control | Pins are held for the whole layer event. Later nodes LRU-touch the slices instead of re-acquiring them. All reads are still waited for at the first expert node. |
| `kind` | C, the deferred-wait scheduler | Same bookkeeping as C0. Each node waits only for the reads of the weight kind it consumes, so later kinds stay in flight while earlier nodes compute. |

Every arm requests the same slices in the same order, reads the same bytes, makes the same LRU accesses and uses the same budget. There is no prediction, no lookahead across layers and no new thread.

Configurations that are refused at startup (`GGML_ABORT`, never a silent fallback):
- any other value;
- `layer_touch` or `kind` with a read mode other than `asincrono`;
- `layer_touch` or `kind` with a retention policy;
- any explicit value together with `ARGINE_PREFETCH` other than unset or `0`.

## Build and test

    bash patches/r5-ets/build-r5-ets.sh <workdir>          # Linux build host with network and root
    g++ -O1 -g -std=c++17 -pthread -I patches/r5-ets -I patches \
        patches/r5-ets/argine-store.cpp patches/r5-ets/argine-research.cpp patches/argine-retention-policy.cpp \
        patches/r5-ets/argine-ets-test.cpp -o etstest && ./etstest <scratch-dir>

Protocol: `experiments/R5-00-exposed-transfer-stall`. Implementation record and gate: `experiments/R5-01-ets-implementation`. Build identity in force: `llama-research-r5-ets-2/BUILD-INFO.json`. The first build, `llama-research-r5-ets/BUILD-INFO.json`, is kept unchanged as a record; it was superseded after the host incident (`experiments/R5-01-ets-implementation/G1-ATTEMPT-1-INCIDENT.md`).
