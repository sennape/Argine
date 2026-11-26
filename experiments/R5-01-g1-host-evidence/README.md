# R5 Phase 1a — canonical G1 host evidence (sealed)

**Verdict: `R5_G1_HOST_CONFORMANCE_PASS`**, 17 of 17 criteria. I recomputed it from the committed copies with the committed driver (`host_conformance.py evaluate`). An independent verifier re-derived it with separate code.

This is **implementation and host-conformance evidence** on the four development prompts. It is not a runtime speedup. No timing contrast between C or C0 and A is computed or reported, the evaluation stays locked, and the headroom bound H (Phase 1b) has not been computed.

| file | content |
|---|---|
| `host/` | the canonical campaign's records and launch logs, from `%USERPROFILE%\AppData\Local\Argine\r5\phase1a-host-g1-v2` (42 launches). One transform only: the user-profile prefix becomes `%USERPROFILE%`, and CRLF becomes LF. |
| `MANIFEST.json` | every file of the host directory: size and raw SHA-256 of the original; for committed files, the SHA-256 of the committed copy |
| `G1_SEAL.json` | the recomputed verdict and criteria, the identities and the provenance |
| `IMPLEMENTATION_FREEZE.json` | what R5-00 `IMPLEMENTATION_FREEZE` requires before G2: runtime-source hashes, binary hashes, model and store hashes, implementation choices |
| `FREEZE.json`, `g1_seal.py` | the seal and its verifier (`python g1_seal.py verify [--host-dir DIR]`) |

**Identities:**

| | SHA-256 |
|---|---|
| R5 runtime (`llama-research-r5-ets-2/llama-server.exe`) | `da6e7183e44f6f69d58156cf683f5a929f16fc74bba442fdea39f139e4007493` |
| R5 model-free test (`argine-ets-test.exe`) | `0b8275a031ea100e58a970872fcaf92898bdcb97822f95a9f89964cd290f8ce6` |
| v5 comparator (reconstructed) | `1ed0f0037a56b14abb3b4ce6706e166c489f9166f6be6b22f491a39c1f521c12` |
| model (`Qwen_Qwen3-30B-A3B-Q4_K_M.gguf`) | `a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32` |
| store (`.psxs`) | `fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146` |

**Disclosures:**
- The historical llama-research-v5 executable (`65f445c4…`) was lost. The v5 comparator is a **deterministic reconstruction** from the frozen v5 source/toolchain/build contract (`../R5-01-ets-implementation/V5-RECOVERY.md`). It is not the original recovered byte for byte.
- Windows TLS incident. The second host attempt's model-free test died with STATUS_HEAP_CORRUPTION, and the driver of that time carried on (`../R5-01-ets-implementation/G1-ATTEMPT-1-INCIDENT.md`). Commit `4a7f308` corrected this: per-thread storage, fail-closed prerequisites, and a new build. This campaign ran that build after a minimal model-free validation (exit 0, 143/0/3).
- Noncanonical attempts. These directories are listed in `MANIFEST.json`. They are kept on the host and contribute nothing:
  - `phase1a-host`: refused before any launch;
  - `phase1a-host-v5rec`: the interrupted heap-corruption attempt under the superseded runtime;
  - `ets-modelfree-v2`: model-free validation only.
- The frozen G1 text also asks for "0 redundant transfers". This is not one of the driver's 17 criteria. It is covered by the model-free conformance test (same requests and bytes; `MODEL_FREE_CONFORMANCE.json`) and, in the two traced launches, by read records equal to store misses.
