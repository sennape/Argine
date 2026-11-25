# First G1 host attempt — heap-corruption incident (noncanonical)

## What happened
The first development-only G1 host run used the reconstructed v5 comparator. Its mandatory Windows model-free test, `argine-ets-test.exe` (first R5 build, sha256 `133a5ed0…`), ended with:
- exit code `3221226356`, which is NTSTATUS `0xC0000374`, STATUS_HEAP_CORRUPTION;
- no output at all, and therefore no `RESULT` line.

The driver did not stop. It ran the startup smoke and started the 30B workload campaign. Two launches finished, a third was interrupted, and then the run was stopped.

**The attempt is not canonical G1 evidence.** Its output directory (`%LOCALAPPDATA%\Argine\r5\phase1a-host-v5rec`) is preserved untouched as diagnostic evidence. None of its launches is reused. The corrected driver refuses to resume into any directory that holds launches or identities of another runtime, and the restarted campaign uses a new directory. Nothing from that directory was copied into the repository.

## Root cause
Every Argine Windows build uses the mingw-w64 GCC 10 toolchain (`10-posix 20220113`). With that toolchain:
- a `thread_local` object lives in libgcc's emulated TLS (emutls);
- its destructor is registered through libstdc++'s generic `__cxa_thread_atexit` fallback (libsupc++ `atexit_thread.cc`).

At thread exit, both are cleaned up by winpthreads pthread-key destructors (`_pthread_cleanup_dest`), which run in ascending key order. The emutls key is created by the first TLS access, so it always has a lower index than the libstdc++ key, which is created when the first `thread_local` with a destructor is constructed. As a result:
1. `emutls_destroy` first **frees** every `thread_local` object of the exiting thread;
2. then `~StatoThread` (the store's per-thread state), `~ThreadBuf` (the trace buffer) and the others run **on the freed blocks**, following stale `std::deque`/`std::vector` pointers.

The model-free test exposed this because each test rig starts and joins its own thread team, so dozens of threads that used the store exit. llama-server keeps one thread pool, so in production the defect is latent until the pool's threads exit at shutdown. That is also where `~StatoThread` computes the G1 counter `drained_at_thread_exit`.

**Why Linux missed it.** glibc runs `thread_local` destructors (`__cxa_thread_atexit_impl`) *before* it releases TLS. So the Phase 1a Linux, ThreadSanitizer and AddressSanitizer runs could not see the defect.

**Evidence.**
- *Sources:* winpthreads 8.0.0 `thread.c` (`_pthread_cleanup_dest` iterates keys 0..keymax; `pthread_key_create` allocates ascending), GCC 10.3 `libgcc/emutls.c` (`emutls_destroy` frees each object), and `libsupc++/atexit_thread.cc` (key-based fallback, `run`).
- *Binary:* the first test binary contains `__emutls_v.…mio_stato…st`, `__emutls_get_address`, libstdc++'s own `__cxa_thread_atexit` (with its `once` key) and `_pthread_cleanup_dest`.
- *Reproduction:* `patches/r5-ets/mingw-tls-emulation.cpp`, built with `clang++ -femulated-tls`, makes Linux use the same teardown order. Under AddressSanitizer, the **unchanged** first-build sources of `argine-ets-test` stop with `heap-use-after-free` in `~StatoThread()`, called from the fallback's `run()` after libgcc freed the block. They print no `RESULT` line, because stdout was buffered and lost in the abort, exactly as on the host. The corrected sources under the same emulation pass: 143 passed, 0 failed, 3 skipped, ASan clean.

The scheduler (`argine-ets.h`) and the arm logic are not involved. The defect is in the per-thread storage of runtime state, common to the store, the trace and the hook.

## Correction
- **`patches/r5-ets/argine-tls.h`.** `argine_thread_object<T, Tag>()` replaces every `thread_local` with a non-trivial destructor in the R5 runtime sources: the store's `StatoThread` and `eventi`, the trace `ThreadBuf`, and the hook's pin list, ETS state and routing buffer.
  - On mingw the object lives on the heap behind its own pthread key. The key destructor deletes it while it is alive. The main thread's objects are destroyed at `exit()`, as libstdc++'s fallback did.
  - Elsewhere it remains the plain `thread_local`.
  - Arm semantics, requests, residency, eviction order, LRU and budgets are unchanged.
- **Regression test.** `argine-ets-test.cpp` group 11, plus unbuffered stdout so a crash keeps its output, plus the Linux emulation above.
- **Driver.** `host_conformance.py` now fails closed before any model launch:
  - a non-zero model-free exit code, or a missing, malformed, duplicated or wrong `RESULT` line, stops the run before the model is touched;
  - a failed `--version` or configuration refusal stops it before any workload launch;
  - both write `PREREQUISITES.json` and exit with code 3.
- **New build.** `llama-research-r5-ets-2/BUILD-INFO.json`. The first build's record is kept unchanged.
