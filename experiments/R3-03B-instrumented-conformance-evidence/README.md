# R3-03B instrumented conformance evidence

Classification: **R3_03B_INSTRUMENTED_CONFORMANCE_PASS** (correctness only; no timing claim).

Runtime `8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5` (MSVC 19.44.35229, llama.cpp 254b177307b14d055bbe4c050c9c441a1e06d8a4) was run in four launches (b_selected and predicted_protect_sham at 8 GiB, timing OFF and ON). Host records are in `host_records/`; the 10.2 GB traces are identified by sha256 in `EVIDENCE_MANIFEST.json`.

- `validate_evidence.py --quick` re-derives every PASS condition from the committed records (73 checks); `--traces-dir <dir>` additionally re-hashes and re-scans the traces.
- `INDEPENDENT_VERIFICATION.json` records the separate trace re-hash and structural scan.
- `CONFORMANCE_EVIDENCE.json` holds the summary, canonicalization method, pass conditions and limitations.
- `FREEZE.json` holds the conformance freeze SHA-256.

Prior frozen evidence is not modified.
