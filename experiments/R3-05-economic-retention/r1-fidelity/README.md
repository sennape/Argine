# R3-05 rung R1 — frozen stream extraction + replay fidelity

Classification: **R3_05_R1_FIDELITY_PASS** (see FIDELITY_RESULTS.json). Model-free; no candidate evaluated.

- `SOURCE_MANIFEST.json` — the three frozen R3-03A traces (A-4GiB primary, B-4GiB, C-8GiB) bound by size/SHA-256/token digest/runtime hash.
- `CANONICAL_STREAM.jsonl` — the 24,672-event canonical stream (514 passes x 48 layers) used by later rungs. Raw traces (5-10 GB) stay on the host.
- `STREAM_MANIFEST.json` — stream hash, cross-trace equality, byte-determinism evidence.
- `FIDELITY_RESULTS.json` — replay of A and PP at 1.5 GiB vs the Gate 2 counters (all integer-exact).
- Re-verify offline (about 1-2 min): `python experiments/R3-05-economic-retention/r1-fidelity/r1.py verify`
- Tests: `python -m unittest discover -s experiments/R3-05-economic-retention/r1-fidelity/tests`
