# llama-research-v5 recovery (R5 Phase 1a)

**Verdict: `R5_V5_DETERMINISTIC_RECONSTRUCTION_ACCEPTED`** — details in [`V5-RECOVERY.json`](V5-RECOVERY.json).

## Why it was needed
Gate G1 of the frozen R5-00 protocol compares the R5 build's baseline arm A with "the pre-R5 runtime (llama-research-v5)": tokens must be identical and decode must be no more than 2% slower. The v5 `llama-server.exe` was an untracked local file and was deleted in the intentional local cleanup. Only `llama-research-v5/BUILD-INFO.json` survives, and it is unchanged.

The first G1 attempt refused at its identity check, before any model launch. No G1, timing or token output exists, so the recovery was done without any knowledge of outcomes.

## What was done
1. **Search.** No copy of the original binary was found in the repository, on the Desktop or in the user profile.
2. **Reconstruction.** The v5 source state was taken from Git: commit `34997c2`, `patches/` tree `03fe2f75`. It was rebuilt with the v5 build contract recorded in the frozen history:
   - llama.cpp `254b177`;
   - patch order 0002, copy of the argine sources, 0001, 0003;
   - 0004 applied as its edit, which yields exactly its post-image blob `8f9d51d`;
   - the historical `/tmp/mingw.cmake` toolchain file and CMake flags;
   - the v3–v5 `_WIN32_WINNT=0x0A00` deviation;
   - Ubuntu 22.04 mingw-w64 GCC `10-posix 20220113`, mingw-w64 8.0.0, CMake 3.22.1 and Ninja 1.10.1, all from pinned packages;
   - source path `/home/claude/llama.cpp`.

   The build script is [`v5-recovery/build-v5-recovery.sh`](v5-recovery/build-v5-recovery.sh).
3. **Two independent clean builds.** In all four executables, the raw bytes differ only in `[136,138)` and `[216,218)`. Those ranges lie inside the COFF TimeDateStamp and the optional-header CheckSum. The PE-normalized SHA-256 values, which zero exactly those 8 bytes, are identical.
4. **Historical hash.** No timestamp from 2026-11-14 14:18Z to 2026-11-15 02:18Z, each with its recomputed checksum, turns the reconstruction into the historical raw SHA-256. The reconstruction is therefore **not** byte-identical to the lost original beyond the PE metadata either. The frozen record leaves several inputs open: whether a web UI was embedded, clone depth (build number), and the original path/prefix. The original's normalized digest was never frozen.
5. **Semantic checks.**
   - The sources equal the `34997c2` blobs.
   - All 20 string literals that v5 added in code are in the binary.
   - None of the literals added later, from post-v5 or R5 sources, are in it.
   - The DLL import shape is the same as v3–v5.
   - The v5-era model-free store regressions pass, including R2-12 integration sections 11–12.

## Independent review
A separate review agent tried to invalidate the recovery. It worked read-only on the evidence and made its own byte comparison, patch re-application and variant build. Its conclusion: **ACCEPT, 0 material findings.**

Its variant build put the `_WIN32_WINNT` define in the C++ flags only. The objects came out byte-identical and the normalized hash was the same, so the way the define was injected does not matter.

All 8 minor findings are resolved as disclosures in `V5-RECOVERY.json` (`review`, `disclosures`). The main one: `llama-server`'s build embeds a web UI downloaded from HuggingFace when it has network access. The reconstruction embedded none, and the build contract now requires that. This is the likely main reason the historical raw hash is not reproduced. It does not touch `/health`, `/completion` or the expert-store path.

## Identity statement
The accepted identity is the **deterministic normalized identity of a reconstruction from the frozen v5 source/toolchain/build contract**. It is *not* claimed to be the normalized digest of the original binary.

The historical raw SHA-256 `65f445c4…df26c` remains the original identity.

| | value |
|---|---|
| historical raw SHA-256 | `65f445c42ac145765735984582691964181dd4dd8019f4519edac1fa315df26c` |
| reconstructed raw SHA-256 (build 1, the accepted file) | `1ed0f0037a56b14abb3b4ce6706e166c489f9166f6be6b22f491a39c1f521c12` |
| reconstructed PE-normalized SHA-256 (builds 1 and 2) | `a3164821861a56dec259e1e977cdfc6c805bffd062a2ab379f7b4a02843c7815` |

## Driver rule
`host_conformance.py` (`v5_identity`) accepts the v5 comparator only in two cases:
- the historical raw hash; or
- the ONE reconstruction. For this the binary must have the pinned raw hash and the pinned normalized hash, and `V5-RECOVERY.json` must agree with the driver on the verdict, the review, the historical hash, the source commit and tree, the llama.cpp commit, the compiler and the build-contract digest.

Every other binary is refused, including another clean rebuild that differs only by timestamp. The identity used is written to `IDENTITY.json` and `G1_HOST_RESULT.json`.

The binary itself stays out of Git, in `llama-research-v5-recovered/`, which is excluded locally.
