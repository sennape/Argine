# R3-01 — Telemetry Realization and Admission Harness

Status: **PASS_TELEMETRY_REALIZED** (infrastructure only; no R3 scientific result exists).

This phase implements, offline, the machinery the frozen R3-00A observability contract requires:
a retention-decision trace becomes a contract-shaped `TRACE_COUNT` record, measured wall-clock
becomes a `TIMING` record, the two are paired into one scientific cell, and the pair is admitted
or rejected fail-closed. Nothing here runs the model, opens DEV/EVAL, selects a policy, changes
C++, or modifies R2/R3-00A artefacts.

Frozen inputs (unchanged, re-verified before and after):

| artefact | digest |
|---|---|
| `R3-00A/OBSERVABILITY_CONTRACT.json` | `99a68c73c5a3caba4fd53937f27907375734f3866c84e18d4ecb944013efa073` |
| `R3-00A/CANONICAL_R2_12_AUDIT.json` | `eb94bc2ce783b417a4590c2acc60d78cd4e1d452a132e6258575cea10bcc1053` |

## Record types

**TRACE_COUNT** — derived from the decision trace. Exact: `expert_bytes_read`,
`expert_miss_bytes`, `resident_bytes_peak`, `evictions`, requested/hit/miss expert events.
Derived: `expert_read_count = 3 × expert_miss_count` (gate/up/down), explicitly labelled
`DERIVED_RUNTIME` — never promoted to exact.

**TIMING** — wall clock and process CPU: `ttft_seconds`, `decode_duration_seconds`,
`tokens_generated`, `process_cpu_seconds`, and the mean `decode_step_wall_time`. Policy overhead
is reported as counters (`events_served`, `process_cpu_seconds`) with `isolated: false`; no
overhead isolation is claimed.

**CELL** — `TRACE_COUNT + TIMING` of one physical configuration. Counts come from the trace, time
from the timing run; neither record alone is analysable.

## Pairing semantics

A cell is admissible only if both records carry the same `workload_id`, `seed`, `budget_bytes`,
`policy_id`, `identity.policy_params_sha256` and an identical `output_token_digest`, plus the
same contract digest and schema version. The output digest is the mechanical proof that enabling
the decision trace did not change what the model produced.

## Output-token digest semantics (v1)

```
sha256( b"argine-output-tokens-v1\n" + "".join(f"{t}\n" for t in tokens) )
```

It depends only on the ordered completion token ids: independent of prompt text, JSON
formatting, logging mode and host; order-sensitive; defined for the empty sequence.

## Trace archival semantics

The trace is **referenced and hashed, never copied**: `{path, inside_repository, sha256, bytes,
trace_format, copied_into_repository, copy_policy}`. Traces above 8 MiB are never written into
Git; the archive entry points at the artifact and pins its digest.

## Admission

`ADMISSIBLE` / `INADMISSIBLE` with explicit failures, using the frozen R3-00A rule identifiers
(V1–V10, C1–C2) plus the R3-01 X-rules:

| rule | meaning |
|---|---|
| X1_CONTRACT_DIGEST | both records carry the frozen contract digest |
| X2_SCHEMA_VERSION | supported telemetry schema version |
| X3_SEED_PRESENT | a scientific cell cannot have an unknown seed |
| X4_SCIENTIFIC_STATUS | allowed, identical status (`CELL_READY` / `OFFLINE_FIXTURE`) |
| X5_OUTPUT_DIGEST | both records carry a token digest |
| X6_TRACE_BUDGET | the trace budget equals the record budget |
| X7_COUNTER_INVARIANTS | requested = hits + misses, miss bytes = bytes read, reads = 3 × misses |

Failures are never dropped: the admission object lists every failed rule.

## Offline validation

`OFFLINE_VALIDATION.json` records 16/16 passing cases: valid records and cell; missing
TRACE_COUNT/TIMING telemetry; mismatched workload, seed, budget, policy params, output digest;
incomplete trace; budget violation; non-hex identity; altered contract digest; a canonical
R2-12 record; and a full offline extraction of the real conformance trace
(`9504` decision rows, `9504` completion rows, completeness `COMPLETE`).

The canonical R2-12 sweep over all **161** top-level records yields **0 admissible**, as required.

The real conformance trace is used only as a parser/extractor fixture; it covers a single layer
and is not R3 evidence.

## Determinism

Every generated record is serialized with sorted keys and fixed separators. Rebuilding the
fixture record twice gives byte-identical files and identical digests (tested).

## Reproduce

```bash
python build_artifacts.py                 # regenerate OFFLINE_VALIDATION.json + IMPLEMENTATION.json
python build_artifacts.py --created-utc <ISO8601> --anchor-commit <sha>   # also FREEZE.json
python -m unittest discover -s tests      # 33 tests, standard library only
```

The real-trace pass takes ~30 s (1.8 GB hash + stream).

## Not frozen here

Cost-aware policy, cost-model coefficients, primary scientific endpoint, DEV selection rule,
EVAL thresholds, runtime benchmark endpoint.

## Known limitations

* The conformance fixture traces a single layer, so its counters are not a full-run I/O total.
* Policy overhead remains non-isolated; a sham-policy control is required before any
  overhead-difference claim.
* Trace-derived counters inherit the trace's completeness guarantee: an incomplete tail makes
  the record inadmissible rather than approximate.
* No real TRACE_COUNT+TIMING pair exists yet; the pairing path is proven on synthetic cells and
  the extractor on the real trace.
