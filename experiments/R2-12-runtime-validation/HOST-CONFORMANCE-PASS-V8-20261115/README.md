# R2-12 host-conformance pass, v8, 2026-11-15

This directory is the additive canonical preservation of the real Windows-host
run that produced `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` (see
`../R2_12_RUNTIME_POLICY_CONFORMANCE_PASS.json`). It does not move or delete
anything in `tools/r2-12-conformance-run/` (the working directory the harness
writes to on each run, subject to being overwritten by a future run) -- every
file below is a byte-for-byte copy of an artifact that was actually produced
by that run, verified by SHA-256 at copy time.

Runtime under test: `llama-research-v8` (`f4d93061b0d4ce41bc4c5f0abce3420e16768ff5dba5ce53212b49ca25dcf087`).
Model: `C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf`. Store: `C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs`.
Budget: 4096 MiB. Harness: `tools/r2-12-host-conformance.ps1`.

The raw `ARGINE_RETENTION_TRACE` JSONL for each policy (decision rows
interleaved with post-service completion rows) is preserved separately under
the established naming convention in `../conformance_traces/`:
`conformance-b_selected-20261115.jsonl`, `conformance-predicted_protect-20261115.jsonl`.
This directory holds everything else: the decision-only subset the comparator
actually consumes, the host stdout/stderr, and the independent re-verification
performed from this preservation (not from the live host run).

## Two distinct verifications -- do not conflate

There are two separate verification events in this evidence set, of
different status, and they must not be presented as equivalent or merged:

1. **The real host comparator run (authoritative).** As part of the same
   campaign execution that produced this evidence, `tools/r2-12-host-conformance.ps1`
   itself invoked the unmodified frozen comparator,
   `tools/r2_12_real_trace_conformance.py`, directly on the Windows host
   (full RAM available there; no memory constraint) against each policy's
   `*.decisions.jsonl` as it was generated. This produced the result recorded
   in `../R2_12_RUNTIME_POLICY_CONFORMANCE_PASS.json` and in
   `CONFORMANCE-MANIFEST.json`'s `run_result.comparator_result`:
   `b_selected = MATCH` (9504 events), `predicted_protect = MATCH` (9504
   events), overall `R2-12 HOST CONFORMANCE: BOTH MATCH`, harness exit 0.
   **This host-side run of the actual frozen comparator is the authoritative
   conformance result.**

2. **The sandbox streaming audit (supplemental, post-hoc).** This
   formalization was carried out in a separate, memory-constrained sandbox
   (~3.9 GiB RAM, no swap) that cannot run the frozen comparator's
   `f.readlines()`-based, fully-in-memory implementation against the
   preserved ~1.9 GiB trace files without being OOM-killed. `streaming_conformance.py`
   (in this directory) is an additive, non-modifying driver written only to
   let this agent independently re-check the *preserved copies* of the
   evidence under that constraint, by importing the identical frozen policy
   classes and replaying the identical comparison logic across two bounded
   passes. Its result (see "Independent re-verification result" below and
   `comparator-b_selected.json` / `comparator-predicted_protect.json`) is
   **supplemental corroboration that the preserved files are internally
   consistent with the frozen policies**, not a replacement for, not equal
   in status to, and not the source of, the host's own MATCH finding in (1).
   `tools/r2_12_real_trace_conformance.py` itself was never modified and was
   not re-run by this agent anywhere it would need to be -- it already ran,
   successfully, on the host.

## Preserved artifacts

| Canonical path (this dir unless noted) | SHA-256 | Bytes | Role | Policy | Source path (on the mounted host tree) |
|---|---|---|---|---|---|
| `../conformance_traces/conformance-b_selected-20261115.jsonl` | `3ae926280e0fb092aabf196cacb98fe59742901fd4ba8fc1217b00bdd2fd384f` | 1921137735 | raw ARGINE_RETENTION_TRACE (decision+completion rows) | b_selected | `tools/r2-12-conformance-run/b_selected.trace.jsonl` |
| `../conformance_traces/conformance-predicted_protect-20261115.jsonl` | `ae872d509591eae0c1a8b6fc699989b41396e70b8cdf6154129206a948af6c4f` | 1921816398 | raw ARGINE_RETENTION_TRACE (decision+completion rows) | predicted_protect | `tools/r2-12-conformance-run/predicted_protect.trace.jsonl` |
| `b_selected.decisions.jsonl` | `4debf9f4a199313834b8d9b5bc83e40fb2ec74cad0ea9d2660ce4551691b9ce4` | 1920426120 | decision-only subset (comparator input, per tools/r2_12_real_trace_conformance.py's REQUIRED_FIELDS) | b_selected | `tools/r2-12-conformance-run/b_selected.decisions.jsonl` |
| `predicted_protect.decisions.jsonl` | `2a4e955d988b69327c11560863df5fda24fcd751716bafe6e01c6bf089da2475` | 1921104783 | decision-only subset (comparator input) | predicted_protect | `tools/r2-12-conformance-run/predicted_protect.decisions.jsonl` |
| `b_selected.stdout.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 0 | host process stdout (empty -- llama-server writes its own logging to stderr; absence of stdout content is expected, not a capture failure) | b_selected | `tools/r2-12-conformance-run/b_selected.stdout.log` |
| `b_selected.stderr.log` | `ebdf02c144a812e826bd3b51c00b9bd1e0e71b693af37462f35a0e26ebedbb06` | 2534 | host process stderr (startup markers, one hit-sans-index self-heal note, timing) | b_selected | `tools/r2-12-conformance-run/b_selected.stderr.log` |
| `predicted_protect.stdout.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 0 | host process stdout (empty, expected) | predicted_protect | `tools/r2-12-conformance-run/predicted_protect.stdout.log` |
| `predicted_protect.stderr.log` | `c1db75e861875ed6d9a298a4b0ee302dfe4e3c30d32a789359c2b38e28b84799` | 2570 | host process stderr | predicted_protect | `tools/r2-12-conformance-run/predicted_protect.stderr.log` |
| `mechanical-audit-b_selected.json` | `24b91fb1e0caeb9c8b6385090d7f96d1d06212c01bf47035b8838514251f6b09` | 937 | this formalization's own structural audit of the preserved trace (row counts, budget-violation scan, step/eviction stats) | b_selected | generated from the preserved trace above, not from a host path |
| `mechanical-audit-predicted_protect.json` | `e987b6da2904716878c6b0fbb213f91bdabe304727a298451c756111c5d50f7e` | 956 | same, predicted_protect | predicted_protect | generated |
| `streaming_conformance.py` | `fbee8539efe578c55e59c972d31035b42617cf22c6e61368c14ceea2531a154a` | 10085 | this formalization's memory-streaming driver (see header comment) used for the independent re-verification below; does NOT modify or replace `tools/r2_12_real_trace_conformance.py` | both | generated for this formalization |
| `comparator-b_selected.json` | `06a31730346a3ec444f30c0574d9a4df0c91ba9b73fbfc3f646679f00195bc72` | 15055 | result of independently re-running the frozen-policy comparison against the preserved `b_selected.decisions.jsonl` | b_selected | generated |
| `comparator-predicted_protect.json` | `7cec02035190c12ba2ef9ae566e26c0372183e5e2958f0cf6f0aafebf08e9ef2` | 15045 | same, predicted_protect | predicted_protect | generated |

## Why `streaming_conformance.py` exists

`tools/r2_12_real_trace_conformance.py` (unmodified, unchanged by this
formalization) reads its whole input with `f.readlines()` and materializes
every decision event into one Python list before replay. On the ~1.83 GiB
`*.decisions.jsonl` files here (9504 events each, growing resident arrays),
this OOM-killed (exit 137) in the sandbox used for this formalization
(≈3.9 GiB RAM, no swap). `streaming_conformance.py` is a separate script
that imports the exact same frozen classes
(`argine.research.policies.Context`, `argine.research2.r2_09a.policies.AgedFrequency`,
`argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy`) and
reproduces, field-for-field, the same `_build_policy`/`_patch_evict_one`/
`_protected_for_layer`/comparison logic as `compare()` in the original tool,
restructured into two streaming passes so no more than one event is held in
memory at a time. It does not alter, and was not used in place of, the
original tool anywhere it could actually run; it is additive tooling for
this preservation, not a replacement for `tools/r2_12_real_trace_conformance.py`.

## Sandbox streaming-audit result (supplemental; the host's own comparator result in (1) above is authoritative)

| | b_selected | predicted_protect |
|---|---|---|
| Comparator result | MATCH | MATCH |
| Events compared | 9504 | 9504 |
| Final resident bytes (frozen Python) | 4292702208 | 4294361088 |
| Final resident bytes (real runtime, last completion row) | 4292702208 | matches corresponding completion row |
| Total evictions (frozen Python) | 8988 | 7546 |
| Decision rows (mechanical audit) | 9504 | 9504 |
| Completion rows (mechanical audit) | 9504 | 9504 |
| Malformed lines | 0 | 0 |
| Budget violations (post-service or policy-resident) | 0 | 0 |
| First step requiring eviction | 354 | 354 |
| Steps with at least one eviction | 2839 | 2581 |
| Max resident bytes observed | 4294950912 (budget 4294967296) | 4294950912 (budget 4294967296) |

Step 354 is the exact event that caused v7's `GGML_ABORT` (exit code 3) on
the real host, documented in `RETENTION-POLICY-TRACE-INSTRUMENTATION.md`
section 4 and in the `patches/argine-store.{h,cpp}` same-event
self-eviction fix (commit `02dd25f`). Both preserved v8 traces pass through
this exact step, and thousands more eviction-wall events after it, without a
single budget violation -- this is the first real-world (not just local
regression) evidence that the fix holds under the real runtime.
