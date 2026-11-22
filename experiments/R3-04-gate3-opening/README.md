# R3-04 Gate 3 tooling (prepared; OPENING_FREEZE not yet created)

* `tools/r3_04_host_identity.py` — model-free host attestation (hashes the frozen model, store and runtime; never launches llama-server).
* `tools/r3_04_open_gate3.py` — creates `experiments/R3-04-net-runtime-value/OPENING_FREEZE.json` only from a matching attestation, verified protocol/Gate 2/amendment freezes and a committed, unmodified driver.
* `validate_opening.py` — recomputes every binding of an existing OPENING_FREEZE.json.

The campaign stays unauthorized until Luca explicitly authorizes it.
