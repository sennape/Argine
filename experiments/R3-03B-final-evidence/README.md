# R3-03B final evidence

Independent post-acquisition audit of the R3-03B end-to-end campaign (80 counted launches, 20 ABBA blocks).
`validate_final.py` (stdlib only, imports nothing from `tools/`) recomputes everything from the raw records
(`--run-dir`) or from the committed non-token summary (`--quick`). Raw records stay on the host; identified by SHA-256 in VALIDATION_MANIFEST.json.
Orphan record 005.try1 has no timing fields and weight zero. Resume classified PROTOCOL_PRESERVING. Duration excess is operational and not used in any estimand.
See FINAL_RESULT.json, CAMPAIGN_AUDIT.json, CLAIM_BOUNDARIES.md.
