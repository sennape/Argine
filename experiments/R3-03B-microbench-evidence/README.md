# R3-03B authoritative microbenchmark evidence (run2)

Classification: MICROBENCH_COST_ATTRIBUTION_READY. See MICROBENCH_RESULT.json (coefficients, CIs, components, timer, VM and historical comparisons, limitations).

- host_records/: small host files (report, per-process meta and replay rows, identity records), bound by EVIDENCE_MANIFEST.json.
- Large raw per-call and calibration files (~76 MB) remain on the host at C:\Users\alpel\Desktop\Argine4-r3runs\R3-03B-microbench-run2\raw and are bound by size and sha256 in the manifest.
- validate_evidence.py: independent recomputation (52 checks). `--quick` verifies only the committed host_records.
- run1 (R3-03B-microbench-run1) is PREFLIGHT_REJECTED_NO_TIMING_ACQUIRED and is not evidence.
- The frozen protocol (PROTOCOL.json) was not modified.
