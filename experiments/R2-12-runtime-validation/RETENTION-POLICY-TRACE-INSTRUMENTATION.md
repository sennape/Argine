# R2-12 -- Retention-Policy Real-Trace Instrumentation & Comparator

Status: instrumentation + comparator + synthetic tests only. **No real-model
execution, no benchmark, no `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` artifact
was produced in this phase.** This document, the trace schema it describes,
and `tools/r2_12_real_trace_conformance.py` are strictly additive
instrumentation on top of the real-runtime integration already committed in
`ae71fbe` / `8a96d09`. No retention, eviction, predictor, or budget decision
was changed as part of adding this instrumentation (one pre-existing
integration-layer safety gap, found while producing real traces for this
phase, is hardened -- see "Integration-layer finding" below; it changes no
frozen decision, only how the wrapper fails when a decision cannot be
executed safely).

## 1. What this phase adds

1. `argine-store.cpp`'s `servi_evento_con_politica()` now writes one rich
   JSONL line per EVENT (not per eviction) to `ARGINE_RETENTION_TRACE`,
   capturing everything needed to replay and compare a real decision
   against the frozen Python reference, event by event.
2. `tools/r2_12_real_trace_conformance.py` reads that trace and replays the
   identical event sequence through the ACTUAL frozen Python classes
   (`argine.research2.r2_09a.policies.AgedFrequency`,
   `argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy`)
   -- never a reimplementation -- comparing decision-by-decision and
   failing at the first genuine mismatch.
3. `tests/test_r2_12_real_trace_conformance.py` -- 27 synthetic unit tests
   against hand-built, schema-complete trace events (ground truth produced
   by actually running the real frozen policy classes, never hand-computed
   expected values). No real model, no `.psxs` store, no subprocess.

## 2. Final JSONL trace schema (one line per event)

```json
{
  "step": 3,
  "policy": "b_selected" | "predicted_protect",
  "layer": 0,
  "bytes_of_layer": 3059712,
  "requested_experts": [3, 4],
  "hit_experts": [],
  "miss_experts": [3, 4],
  "protected_before": [],
  "resident_before": [
    {"layer": 0, "expert": 0, "score": 2.0, "last_ref": 5}
  ],
  "evicted": [{"layer": 0, "expert": 0}],
  "resident_after": [
    {"layer": 0, "expert": 3, "score": 1.0, "last_ref": 6}
  ],
  "cache_resident_bytes_before": 18358272,
  "cache_resident_bytes_after": 15298560,
  "policy_resident_bytes_after": 15298560,
  "budget_bytes": 18362368,
  "policy_params": {
    "decay_factor": 0.99,
    "aging_interval_steps": 64,
    "predictor_decay": 0.99,
    "top_k": 2,
    "n_experts": 128
  }
}
```

Field notes:

- `requested_experts` / `hit_experts` / `miss_experts`: the deduplicated,
  sorted expert ids for this event, split by whether they were already
  resident before this event's decision.
- `resident_before` / `resident_after`: the FULL global resident set (not
  just this layer), each with its internal `score` and `last_ref`, enough
  to independently verify tie-breaking (`(score, last_ref,
  stable_obj_key)` ascending, exactly AgedFrequency's own key).
- `evicted`: the victims the real runtime's policy instance actually chose
  and physically evicted for this event, in the order chosen.
- `cache_resident_bytes_{before,after}`: the physical cache's own
  byte-accounting (`Shard::resident`), independent of the policy's own
  `resident_bytes_` -- these must always agree with
  `policy_resident_bytes_after`, and the comparator's byte check is one of
  the cross-checks that would catch a desync between the two.
- `policy_params`: provenance, repeated on every line on purpose so the
  comparator can detect a policy or parameter change mid-trace
  (`check_provenance()`), which would make the whole comparison
  meaningless.
- `protected_before`: see the structural gap below -- always `[]` for
  `predicted_protect` in a real trace, for a reason that has nothing to do
  with the real decision being wrong.

## 3. Known structural gap: `protected_before` for `predicted_protect`

`argine-retention-policy.h` / `.cpp` (frozen, commit `c2f9a68`, NOT modified
by this phase) declares:

- `RetentionPolicy::inspect(uint32_t layer_for_protected_query) const` --
  **non-virtual**, on the base class. Its parameter is explicitly unused
  (`/*layer_for_protected_query*/` in the `.cpp`).
- `PredictedProtectPolicy::predicted_next_` -- a **private** member of the
  derived class, with no public accessor anywhere in the frozen module.

The real runtime's integration (`struct psx_cache`) holds the policy only
as `std::unique_ptr<RetentionPolicy>` -- a base-class pointer. Calling
`c->policy->inspect(layer)` therefore can *only* ever invoke the base
method, which structurally cannot reach `predicted_next_` regardless of
which concrete policy is active. `Inspection::protected_experts_for_layer`
is consequently always `[]` in a real trace for `predicted_protect`, even
though the real Python reference has a genuine, non-empty predicted set
after the first event on a layer, and even though
`PredictedProtectPolicy::choose_victim()` DOES correctly consult
`predicted_next_` internally when making its real eviction decision.

**This is a visibility gap, not a decision gap.** The eviction decision
itself (`evicted`, `resident_after`) is made correctly by the frozen
module regardless; only the real trace's ability to independently *display*
the protected set it used is missing. Verified directly: a real
`predicted_protect` trace produced in this phase, replayed through
`tools/r2_12_real_trace_conformance.py`, reports `MATCH` with
`protected_before_gap_events: 6` -- every decision field (`hit_experts`,
`miss_experts`, `evicted`, `resident_after`, `policy_resident_bytes_after`)
matched the real frozen Python reference exactly, across every event,
while the comparator explicitly flagged (never silently dropped) the six
events where it could not independently verify `protected_before` from the
real trace.

Closing this gap for real would require a minimal additive accessor on the
frozen module itself (for example a `virtual std::vector<uint32_t>
protected_for(uint32_t layer) const { return {}; }` on the base, overridden
by `PredictedProtectPolicy` to return its real `predicted_next_[layer]`) --
purely a read-only exposure of already-computed state, changing no
decision. This phase does **not** make that change: it touches a frozen,
already-committed artifact (`c2f9a68`), and that decision is left to the
user rather than taken unilaterally. Until (and unless) that accessor is
added, `protected_before` for `predicted_protect` remains a documented,
non-fatal reporting gap -- `tools/r2_12_real_trace_conformance.py` reports
it explicitly via `protected_before_gap_events` / `protected_before_gap_note`
/ `protected_before_gap_sample` on every run, rather than comparing it
strictly (which would produce a false MISMATCH) or silently skipping it
(which would hide a real API limitation). For `b_selected`, which has no
protected-set concept in the frozen reference at all, `protected_before` is
still compared strictly -- it is legitimately always `[]` on both sides
there, so any disagreement would be a genuine bug, not this gap.

## 4. Integration-layer finding: same-event self-eviction (conformant, but unsafe for the real runtime)

While producing real traces for this phase, a real event sequence
(`{0,1,2}` resident, budget for exactly 3 objects, then event `{3,4}`
requested) hit a hard failure in the eviction-translation loop. Root
cause, verified two independent ways (an isolated C++ probe calling
`BSelectedPolicy::access()` directly with no cache integration, and a
step-by-step replay of the identical event through the real Python
`AgedFrequency` with a monkey-patched `_evict_one`, the same technique
`tools/r2_12_retention_conformance_ref.py` already uses): the frozen
Python reference does **not** exclude members of the *current* event from
victim selection in that same event's later iterations -- an expert just
admitted earlier in one event can legitimately be chosen as a victim later
in that same event. Both replays produced the identical eviction sequence
`[(0,0), (0,3)]` for this scenario -- this is conformant, frozen behavior,
confirmed bit-for-bit, **not a C++/Python conformance defect**.

It is, however, a genuine safety gap in the real-runtime integration layer
(not the frozen policy module): `psx_cache_ensure_many()`'s contract
requires every slice of one event to end up simultaneously resident and
pinned, because a real MoE node needs every requested expert's weights
loaded together for its matmul. A policy decision that evicts a
not-yet-physically-admitted member of its own event cannot be honored by
the physical cache without leaving the policy's logical state and the
cache's physical state permanently desynced.

Two unsafe fixes were tried and rejected before settling on the one now in
`servi_evento_con_politica()`:

- Silently treating it as a no-op (skip the conflicting eviction): rejected
  -- would leave the policy's internal logical state (which believes the
  object was evicted) permanently out of sync with the cache's physical
  state (which keeps it resident), a desync that compounds silently over
  time.
- A hard reject at `psx_cache_attach_retention_policy()` time, using
  `psx_cache_min_budget(s, psx_n_experts(s))` (full expert count) as a
  conservative worst case: rejected as too coarse -- it would refuse many
  reasonable configurations that never actually hit this scenario in
  practice.

The fix kept: a **pre-scan** of `outcome.evicted`, before any physical
mutation, that fails closed immediately (via `s->fail()`, with a message
naming the exact cause and explicitly warning that the `RetentionPolicy`
instance's internal state has already mutated non-undoably for this event
and must not be reused) the moment any victim is found to be a member of
the current event. Verified via a dedicated test section (6 of 6 new
checks passing, `violazioni==0` after the induced failure, confirming no
budget-invariant corruption).

This finding/fix predates this phase's trace-schema work causally (a
clean test harness was needed to produce clean real traces at all) but was
not yet committed separately; it is included in this commit alongside the
instrumentation, clearly identified here as a safety fix to the
integration layer's failure-mode atomicity, not a change to any frozen
retention, eviction, predictor, or budget decision.

## 5. Running the comparator

```sh
python3 tools/r2_12_real_trace_conformance.py /path/to/trace.jsonl
```

Exit code 0 + `{"result": "MATCH", ...}` on a full decision-by-decision
match (predicted_protect's `protected_before_gap_*` keys still appear in
the summary when applicable -- MATCH does not mean the gap was absent, it
means the gap was the *only* thing not independently verifiable). Exit
code 1 + `{"result": "MISMATCH", ...}` at the first genuine divergence, or
`{"result": "BLOCKED_INVALID", "reason": ...}` on a malformed or internally
inconsistent trace.

## 6. What to collect on the Windows host for the next real run

1. Build the real `llama-server` exactly as before (pinned llama.cpp
   commit, same cmake/ninja invocation), with the updated
   `patches/argine-store.{h,cpp}` and `patches/argine-experts.cpp` from this
   commit applied.
2. Set the same environment variables already used to select and configure
   a policy (`ARGINE_RETENTION_POLICY`, `ARGINE_RETENTION_DECAY`,
   `ARGINE_RETENTION_INTERVAL`, `ARGINE_RETENTION_PREDICTOR_DECAY`,
   `ARGINE_RETENTION_TOPK`, `ARGINE_RETENTION_NEXPERTS`), plus the new
   `ARGINE_RETENTION_TRACE=<path to a writable .jsonl file>`.
3. Run whatever real workload is desired (no warmup/benchmark semantics
   implied by this instrumentation itself -- tracing is pure, zero-cost-when-off
   instrumentation on the existing real eviction path).
4. Copy the resulting `.jsonl` trace file back to a machine with this repo
   checked out and run:
   ```sh
   python3 tools/r2_12_real_trace_conformance.py path/to/that/trace.jsonl
   ```
5. Report the JSON result verbatim (`MATCH` / `MISMATCH` / `BLOCKED_INVALID`
   and its full payload). A `MATCH` with a non-zero
   `protected_before_gap_events` for a `predicted_protect` run is the
   expected, documented outcome (section 3) -- it is not itself evidence of
   a problem; a `MISMATCH` on any other field is.
6. This instrumentation does not decide PASS/FAIL for R2-12 benchmark
   purposes by itself -- it only establishes whether the real runtime's
   retention/eviction decisions conform to the frozen Python reference. No
   `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` artifact is produced by this
   tool; that remains a separate, explicit decision for a later phase.
