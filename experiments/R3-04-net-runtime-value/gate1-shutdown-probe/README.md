# R3-04 Gate 1 — model-free shutdown probe (additive evidence)

Tests H1 only (console Ctrl-event delivery to a stand-in Python child). No model was run. The R3-04 protocol freeze (a88359b5…, commit 4a0aa2d) is unchanged; tooling parent is 6b22fdd.

| method | n | failed | median s | max s | >5 s |
|---|---|---|---|---|---|
| legacy | 20 | 0 | 5.40427 | 98.87742 | 10 |
| own_console_helper | 20 | 0 | 0.10764 | 0.14969 | 0 |

Descriptive ratios: median 50.2x, max 660.6x. Classification: SHUTDOWN_PROBE_SUPPORTS_HELPER (H1 only).

Proves: legacy Ctrl-event delivery to the stand-in is unstable (half the trials >5 s, up to ~99 s); the own-console helper delivered the event in <0.15 s in 20/20 trials.
Does not prove: the real llama-server shutdown is fixed, that policy_timing/trace flush on real exit work through the helper, or anything about H2. See FREEZE.json limitations. Real-model validation happens in Gate 2.

Verify: `python validate_gate1.py` (reads PROBE.json, sha256 4ed1abb9…80d2).
