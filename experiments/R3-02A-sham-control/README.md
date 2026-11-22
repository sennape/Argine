# R3-02A — Sham Control Realization and Offline Equivalence

Status: **PASS_SHAM_REALIZED** (infrastructure + offline conformance; no model was loaded).

Adds one additive predictive-retention control, `predicted_protect_sham`, that runs the full
`decayed_transition` bookkeeping while choosing victims exactly as `B_selected`. It is a
behavioural control for R3-02's overhead-isolation question: two policies that should differ only
in bookkeeping cost, not in what they retain.

## Identity and semantics

| | |
|---|---|
| policy id | `predicted_protect_sham` |
| kind | `PolicyKind::PREDICTED_PROTECT_SHAM` (new enum value) |
| params | identical frozen values to `predicted_protect` (decay 0.99, interval 64, predictor 0.99, top_k 8, n_experts 128) |
| `after_event` | **unchanged** — history append, transition-state update, score computation |
| `choose_victim` | delegates to `RetentionPolicy::choose_victim` (the `B_selected` AgedFrequency key) |
| protection | never consulted for victim choice |
| predictor state | never skipped |

Invalid combinations fail closed exactly as before: missing predictor parameters, `top_k = 0`,
intervals outside the frozen pairs or an unknown policy id are rejected (`top_k = 0` is rejected
by the pre-existing validation, so a configuration-only sham was impossible).

## Offline equivalence

`EQUIVALENCE.json` — 6 deterministic event streams (no pressure, repeated reuse, eviction-heavy,
multi-layer, tie resolution, exact budget boundary), sham vs `B_selected`:

* byte-identical decision stream (victim sequence, evicted experts, resident-set trajectory,
  resident bytes after every event);
* identical eviction counts (703 / 1738 / 90 evictions in the three eviction-bearing cases, 0 in
  the two no-pressure cases and the exact-fit boundary case);
* distinct `policy_id`;
* `predictor_decisions > 0` in every case (60–600) — the predictive machinery demonstrably ran.

Regressions: `B_selected` and `PredictedProtect` produce byte-identical output and final stats
between the pre-change build and the sham build on all six streams.

## Build

New, separate runtime identity (the frozen R2 v8 binary is untouched):

| | |
|---|---|
| source | llama.cpp `254b177307b14d055bbe4c050c9c441a1e06d8a4` |
| compiler | MSVC 19.44.35229.0, CMake 3.31.6-msvc6, Ninja 1.12.1 |
| binary | `C:\Users\alpel\Desktop\Argine4-r3build\llama.cpp\build\bin\llama-server.exe` |
| SHA-256 | `3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644` |
| version | `0.5.0-dev (build 1, commit 254b177)`, MSVC x64 |

Two build-portability notes, both additive: `0006-argine-msvc-atomic-shim.patch` maps patch 0001's
GCC `__atomic` builtins onto MSVC interlocked intrinsics (same acquire/release semantics), and
`/DNOMINMAX` is needed because `windows.h` macros collide with `std::min`/`std::max` in
`argine-store.cpp`.

## R3-01 integration

The sham needs **no contract change**: it is an ordinary `policy_id` + `policy_params` pair, and a
TRACE_COUNT record and a TRACE_COUNT+TIMING cell carrying it are `ADMISSIBLE` under the frozen
R3-00A/R3-01 rules (4 tests in `tests/test_r3_01_integration.py`).

## Reproduce

```powershell
# 1. configure + build the conformance tools (baseline from HEAD, sham from working tree)
#    see BUILD_IDENTITY.json for the MSVC environment
# 2. equivalence
python tools/r3_02a_equivalence.py --baseline-exe <baseline.exe> --sham-exe <sham.exe> --out experiments/R3-02A-sham-control/EQUIVALENCE.json
# 3. R3-01 integration
python -m unittest discover -s experiments/R3-02A-sham-control/tests
```

## Not frozen here

R3-02 calibration protocol, acquisition matrix, cost-aware policy, primary scientific endpoint,
DEV selection and EVAL rules. This phase produces a control, not a scientific result.
