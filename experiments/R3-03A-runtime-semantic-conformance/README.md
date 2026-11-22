# R3-03A real-model semantic conformance

**Classification: R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_PASS** (reproduced from the raw traces, not from the console string).

Runtime `e935005e...fd76` (implementation commit 75f22d2, parser recovery commit 9355787). Workload r2-11-coding-v1, 512 decode tokens, seed 42, greedy.
Three launches (A-4GiB `b_selected`, B-4GiB `predicted_protect_sham`, C-8GiB `predicted_protect`) each produced 24,672 policy events (514 passes x 48 layers) with one completion row each.

Verified independently: trace/launch-record hashes, token digests recomputed from the stored token ids (all equal the frozen R3-02 digest `ac28efbe...`), full streaming replay of every trace against the frozen R2 reference (decision by decision, history, decay), zero budget violations, no duplicate/missing events, and per-event trajectory identity of sham and `b_selected`.

Scope and limits: see `CONFORMANCE_RESULT.json` (`proves` / `does_not_prove`). This is correctness evidence only; no timing evidence exists and none is claimed.

Re-validate from the raw host files: `python experiments/R3-03A-runtime-semantic-conformance/validate_evidence.py <evidence_dir>` (`--quick` for hashes + digests only). Raw traces (20.5 GB) are not in git; `EVIDENCE_MANIFEST.json` binds them by size and SHA-256.
