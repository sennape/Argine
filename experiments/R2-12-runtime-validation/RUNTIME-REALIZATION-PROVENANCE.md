# R2-12 Runtime-Policy Realization: Windows Binary Identity Provenance

## Status: additive provenance only. Does not modify any frozen R2-11/R2-12 evidence.

This document exists because R2-12's runtime-policy integration (commits
`ae71fbe`, `70b93d4`, `71bd3c0`) changed source code that the real Windows
runtime binary is compiled from. A compiled binary's identity (its SHA-256)
is a function of its source; changing the source necessarily changes the
hash. This is not a regression, not tampering, and not a frozen-parameter
change — `contract.json`'s `runtime_identity` block correctly continues to
describe the **pre-realization** binary, because that field is a historical
record of what ran during R2-11/R2-12 acquisition, and is left untouched
here. This document is the additive record binding the *new* runtime binary,
needed to realize the already-frozen R2-12 policy semantics on real
hardware, to those same frozen semantics — it supersedes nothing.

## 1. First Windows-host attempt: classified BLOCKED_INVALID

On 2026-11-14 the user's Windows host ran a readiness check
(`experiments/R2-12-runtime-validation/RUNTIME-READINESS-REPORT.json`,
generated 2026-11-14T14:49:35Z, committed alongside this document) which
reported `overall: PASS` with `ARGINE_LLAMA_DIR` resolved to
`C:\Users\alpel\Desktop\Argine2\llama-research-v2` and
`runtime_sha256_matches` confirming `a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3`.
This readiness pass is **correct and not itself in error**: at that moment
`contract.json`'s frozen `runtime_identity.sha256` still names that exact
binary, so the check comparing "configured runtime" against "frozen expected
runtime" had nothing to disagree about yet — the pre-realization runtime was
still the only runtime the contract knew about.

The user then attempted an actual conformance request against that same
runtime. The server started with `build=llama-research-v2@a0063720`, and
**no startup line of the form `argine: retention policy attiva: ...` was
emitted**, because `llama-research-v2\llama-server.exe` predates
`ae71fbe`/`70b93d4`/`71bd3c0` and contains no retention-policy integration
at all — the string does not exist anywhere in that binary (confirmed below,
Section 4). The attempted request also ended in a backend connection reset.

**Classification: `BLOCKED_INVALID` / wrong-runtime-identity.**
This attempt produced no conformance evidence of any kind, valid or invalid,
and must not be read as a policy failure, a decision-logic defect, or a
benchmark result. Benchmark count remains **0/120**, unchanged.

| | |
|---|---|
| Attempt classification | `BLOCKED_INVALID` (wrong-runtime-identity) |
| Runtime that was actually running | `llama-research-v2`, sha256 `a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3` |
| Retention-policy startup line observed | none (correctly absent — this binary predates the integration) |
| Conformance evidence produced | none |
| Benchmark count | 0/120 (unchanged) |
| R2_12_RUNTIME_POLICY_CONFORMANCE_PASS created | no |

## 2. Why realization necessarily produces a new runtime identity

A SHA-256 is a function of bytes, and compiled bytes are a function of
source. `ae71fbe`/`70b93d4`/`71bd3c0` added a new source file
(`ggml/src/ggml-cpu/argine-retention-policy.{h,cpp}`) and modified two
existing ones (`argine-store.cpp`, `argine-experts.cpp`) that are compiled
directly into `llama-server.exe` (and the other three llama.cpp binaries).
Recompiling with this source **must** produce a different binary from one
compiled without it — there is no way to "realize" the runtime-policy
integration in a running binary while that binary's hash stays
`a0063720...`. Preserving the old hash and claiming the new integration is
present would be a false claim; the two are mutually exclusive by
construction. This is the reason requirement #4 ("do not silently overwrite
or pretend it has the old frozen runtime hash") is satisfiable only by
producing a *new*, *differently-named* binary and documenting both
identities side by side, which is what this document and the files below do.

## 3. Old and new runtime identity (side by side)

| Field | Old (`llama-research-v2`) | New (`llama-research-v3`) |
|---|---|---|
| Directory | `Argine2/llama-research-v2/` | `Argine2/llama-research-v3/` |
| `llama-server.exe` SHA-256 | `a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3` | `ea72912f878340bc77e999a3870ace0caf94d532afc9afd14cee0b7296b37a7d` |
| `llama-perplexity.exe` SHA-256 | (unchanged, see old `BUILD-INFO.json`) | `6e212b65d49e5ca150aa1df09e15a1fc6c68ef3478f0b6c65b3046b13240e4e0` |
| `llama-quantize.exe` SHA-256 | (unchanged, see old `BUILD-INFO.json`) | `9c6b036475cae46596e9b8df5ed6dcd5185badd9091666729ff01cf6b1cfc80b` |
| `llama-imatrix.exe` SHA-256 | (unchanged, see old `BUILD-INFO.json`) | `03b05e36ebe33be6d41f979ea81732c229cefc24e00c6e8fc997e773287fd690` |
| llama.cpp commit | `254b177307b14d055bbe4c050c9c441a1e06d8a4` (same) | `254b177307b14d055bbe4c050c9c441a1e06d8a4` (same) |
| argine patch set | 0001/0002/0003 + argine-store/experts/research | 0001/0002/0003/**0004 (new)** + argine-store/experts/research/**retention-policy (new)** |
| Realizes commits | — (predates them) | `ae71fbe`, `70b93d4`, `71bd3c0` |
| Compiler | `x86_64-w64-mingw32-g++-posix (GCC) 13-posix` | `x86_64-w64-mingw32-g++-posix (GCC) 10.3.0-posix` (disclosed deviation, see `llama-research-v3/BUILD-INFO.json`) |
| DLL imports (objdump) | ADVAPI32, KERNEL32, SHELL32, WS2_32, msvcrt | ADVAPI32, KERNEL32, SHELL32, WS2_32, msvcrt (identical — same static-linked, no-OpenMP shape) |
| Runtime executed on real Windows | yes (original build) | **not yet** — built and statically verified only in this Linux cross-compilation environment |

The old binary at `Argine2/llama-research-v2/llama-server.exe` is untouched
and unmodified by this work (verified by re-hashing it immediately before
writing this document: still `a0063720...`).

Full build provenance (toolchain, flags, patch list, deviation notes) for
the new binary is in `Argine2/llama-research-v3/BUILD-INFO.json`, following
the same schema as the existing `llama-research-v2/BUILD-INFO.json`.

## 4. Static verification that the new binary contains the integration

Executed in this Linux build environment via `x86_64-w64-mingw32-strings`
and `objdump` (the binary cannot be *run* here — it is a Windows PE32+
executable):

- `llama-research-v3/llama-server.exe` contains the literal string
  `argine: retention policy attiva: %s (decay=%.6g, interval=%u%s) -- R2-12
  runtime policy integration` — **present**.
- It contains `ARGINE_RETENTION_POLICY`, `ARGINE_RETENTION_PREDICTOR_DECAY`,
  and the associated validation/error strings from
  `argine-retention-policy.cpp`'s env-var parser — **present**.
- `llama-research-v2/llama-server.exe` (the old binary, re-checked as a
  negative control) contains **none** of the above strings — confirmed
  absent, as expected for a binary that predates the integration.
- Both binaries import only system DLLs (`ADVAPI32.dll`, `KERNEL32.dll`,
  `SHELL32.dll`, `WS2_32.dll`, `msvcrt.dll`), confirming the new binary is
  the same static-linked build shape as the old one, not a differently
  configured build.

This establishes, as strongly as static inspection can, that the
retention-policy integration is compiled into the new binary. It does
**not** establish that the binary runs correctly, or that the startup line
is actually printed at process start on real Windows — only execution on
the real host can establish that (R2-12 Phase C requirement #10). This is
explicitly left open below.

## 5. Host-side instructions for retrying conformance

1. Copy (or otherwise transfer) `Argine2/llama-research-v3/` — all four
   `.exe` files plus `BUILD-INFO.json` — to the Windows host, to
   `C:\Users\alpel\Desktop\Argine2\llama-research-v3\`, alongside the
   existing, untouched `llama-research-v2\`.
2. Set `ARGINE_LLAMA_DIR=C:\Users\alpel\Desktop\Argine2\llama-research-v3`
   (replacing the `...\llama-research-v2` value used in the first attempt).
   No other environment variable or code path needs to change for this
   redirection — `argine/runtime.py`'s `ensure_runtime()` resolves
   `llama-server.exe` from whatever directory `ARGINE_LLAMA_DIR` names, and
   labels it `{dirname}@{sha256[:8]}`, so a successful start should now log
   `build=llama-research-v3@ea72912f` instead of
   `build=llama-research-v2@a0063720`.
3. Before sending any real-model request, confirm the server's own startup
   output contains a line of the form
   `argine: retention policy attiva: <policy_id> (decay=..., interval=...)`.
   **This is requirement #10 and is not satisfied by this document** — it
   can only be satisfied by the real Windows process actually printing it.
   If this line is absent, do not proceed; the runtime identity problem is
   not yet resolved and this should be reported back rather than retried
   silently.
4. Only once that line is confirmed present should the real conformance
   procedure from the prior session (b_selected@4GiB and predicted_protect
   traces, `ARGINE_RETENTION_TRACE` into
   `experiments/R2-12-runtime-validation/conformance_traces/`, replayed
   through `tools/r2_12_real_trace_conformance.py`) be attempted again.

## 6. What this document does not do

- It does not modify `contract.json`, or any other frozen R2-11/R2-12
  evidence file. `contract.json`'s `runtime_identity` block continues to
  correctly describe the pre-realization binary used for R2-11/R2-12
  acquisition; that is a historical fact, not a bug.
- It does not run any benchmark. Benchmark count remains 0/120.
- It does not create `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS`.
- It does not claim the new binary has been executed or has emitted the
  startup line on real hardware — only that it is built from source that
  contains the integration, and that the integration's strings are present
  in the compiled artifact.
