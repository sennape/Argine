# R3-05 rung R2 — clairvoyant Belady ceiling / K1 gate

Result: see `K1_DECISION.json` (classification and value), `RESULTS.json` (integer counters at 1.5/4/8 GiB), `ECONOMIC_HEADROOM.json` (kappa conversion), `BELADY_DEFINITION.md` (exact semantics), `SOURCE_IDENTITY.json` (Phase 0 / R1 / stream bindings).
Model-free; consumes only the 2 MB R1 canonical stream (sha256 24fcc3e3...e776). No candidate evaluated.
Re-verify offline: `python experiments/R3-05-economic-retention/r2-belady-ceiling/r2.py verify` (add `--replay` to re-run the 1.5 GiB replays, about 1 min).
