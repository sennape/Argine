# R3-00A — Canonical Telemetry & Observability Contract

Status: **PASS_OBSERVABILITY_READY** (observability frozen; the R3 policy and endpoint are **not** frozen).

This phase exists because R3-00 could not freeze a cost-aware runtime-value endpoint: the
frozen R2-12 campaign contains no expert-memory telemetry. R3-00A turns that finding into a
mechanically enforceable contract, and resolves the audit discrepancy that the R3-00 report
left open.

Nothing here runs the model, opens EVAL, acquires traces, or changes runtime behaviour.
All R2-12 evidence is read-only.

## 1. The 123-vs-120 discrepancy, resolved

| set | count | definition |
|---|---|---|
| top-level files in `runtime_results/` | 161 | 144 launch records + 17 control files |
| launch records | 144 | 120 counted + 24 warmups (8 per budget) |
| **canonical counted** | **120** | 40 each at 1.5 / 4 / 8 GiB |
| pre-canonical trace-enabled attempts | 3 | counted-flagged, **outside** `runtime_results/` top level |
| preflight self-test record | 1 | counted-flagged, no decode metric — not a launch |
| counted if scanned recursively | 123 (+1) | 120 canonical + 3 real attempts (+1 self-test) |

The three extra records are in `PRECANONICAL_TRACE_ENABLED_ATTEMPTS/`, all at 4 GiB on
`r2-11-coding-v1`, with 0.829 / 0.836 / 0.842 decode tokens/s against a canonical ~4.19 —
heavy trace logging slowed them roughly 5×. They were never admitted by the canonical
driver and are classified `PRECANONICAL_TRACE_ENABLED_ATTEMPT`. The earlier "123" figure came
from a scan that required a decode metric, which counted those three and excluded the
self-test.

The canonical rule is frozen in `validator/telemetry_contract.py::select_canonical_runs` and is
structural (top-level file + launch record + `counted` + not `warmup` + budget in {1.5, 4, 8}),
so a future stray `counted: true` file cannot silently enter the set.

## 2. Root cause of the null telemetry

1. **The runtime never exports the counters it computes.** `psx_cache_stats` carries exact
   `hits / misses / evictions / bytes_read / bytes_resident / budget / blocchi / letture`, but the
   only textual export is `Contesto::resoconto()`, which is guarded by `attivo && verbose`,
   prints bytes truncated to MiB (`bytes_read >> 20`), prints no raw read count, and is often
   absent from canonical runs.
2. **The retention diagnostics that *are* unconditional** (`psx_retention_stats`) expose decision
   counts and trace row counts — `events_served`, `predictor_decisions`,
   `trace_rows_attempted / written / completions` — never bytes or reads.
3. **The driver hardcodes the fields to `None`** (`tools/r2_12_benchmark_driver.py`), and only
   parses coverage/band regexes from stderr.
4. **No recovery path exists for the frozen campaign**: all 120 canonical runs have
   `trace_enabled: false`, and no stderr logs were archived.

## 3. The contract

`OBSERVABILITY_CONTRACT.json` (version 1) defines two telemetry modes and their admissibility:

* **TRACE_COUNT** — exact expert-level counters derived from the retention-decision trace
  (already produced by the runtime; observational). Timing from this mode is **inadmissible**
  because tracing slows the runtime ~5×.
* **TIMING** — wall-clock, process CPU and policy bookkeeping counters. It claims **no**
  expert-memory counters.

A comparison cell is the **pair** (TRACE_COUNT + TIMING) of the same physical configuration;
neither run alone is analysable, and the pair must produce identical `output_token_digest`,
which mechanically proves the tracing did not change decisions (`C2_output_identity`).

Fields are classified `EXACT_RUNTIME` (miss bytes, hits/misses, evictions, resident bytes,
requested experts, decision-level expert sets), `DERIVED_RUNTIME` (read count = 3 × expert
misses; mean decode-step time), `MODELED` (queue depth, compute/I/O overlap), `ANALYSIS_ONLY`
(protected-set composition), or `UNAVAILABLE` (per-read timestamps, policy decision/bookkeeping
wall time, protected-set construction time — the runtime does not compute them).

Admissibility is fail-closed: missing mandatory telemetry, non-hex identities, an incomplete
trace, a budget violation, or a mismatched pair all produce `INADMISSIBLE`. No analysis may
consume a rejected record.

## 4. Artefacts

| file | role |
|---|---|
| `OBSERVABILITY_CONTRACT.json` | frozen contract (modes, 16 fields, 10 run rules, 2 cell rules) |
| `CANONICAL_R2_12_AUDIT.json` | generated audit of the 120 canonical runs + every excluded record |
| `FREEZE.json` | hashes, digests, anchor commit, frozen/not-frozen scope |
| `validator/telemetry_contract.py` | canonical-set selector, validator, trace extractor, digest, CLI |
| `tests/test_telemetry_contract.py` | 26 stdlib tests |

## 5. Reproduce

```bash
python validator/telemetry_contract.py audit      # regenerate CANONICAL_R2_12_AUDIT.json
python validator/telemetry_contract.py digest     # print the contract digest
python -m unittest discover -s tests              # 26 tests, no model
```

`FREEZE.json` is regenerated with an explicit timestamp and anchor commit, never implicitly.

## 6. What this contract does *not* freeze

The R3 primary policy, the R3 primary scientific endpoint, the DEV selection rule and the EVAL
decision rules. Those remain open; the observability layer is the prerequisite that lets them be
defined on data that can actually be measured.

## 7. Known limitations

* Per-read I/O timestamps and policy-decision wall time remain `UNAVAILABLE`; a future cost
  model may need them, which would require a contract version bump and a slice-level trace.
* The decision trace is large (≈1.8 GB per conformance run); COUNT runs are therefore expensive
  but exact.
* Policy-overhead isolation still requires a sham-policy control before any overhead-difference
  claim; this contract only requires the counters that make such a control auditable.
