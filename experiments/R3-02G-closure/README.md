# R3-02G closure

Status: `R3_02G_TERMINATED_INCOMPLETE` — reason `DESIGN_SUPERSEDED_BEFORE_UNBLINDING`.
Session 1: `PROTOCOL_DEVIATION_NONCONFIRMATORY_SEALED`.

This is a design supersession, NOT a failed scientific hypothesis. Three things are kept apart:
protocol failure (Session 1 ran without the required cooldowns: the R3-02H execution adapter
`tools/r3_02g_session.py` has no cooldown logic), scientific negative result (none exists), and
voluntary design supersession (independent review + event-granularity audit).

Session 1 is sealed by content hashes only (488 files, root `5cd6c6f6…327b`, `SESSION1_SEAL_MANIFEST.json`).
No A/B timing result was parsed, computed or used. R3-02 stays `MECHANISM_INSUFFICIENT`; frozen
R3-02/R3-02G/R3-02H artifacts are unchanged (hashes in `CLOSURE.json`).

Tests: `python3 -m unittest experiments/R3-02G-closure/tests/test_closure.py`
(set `R3_02G_RUN_DIR` to also re-verify the seal).
