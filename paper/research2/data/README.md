# Frozen II-A evidence snapshot

This directory holds exact, byte-for-byte copies of the specific canonical
evidence files the II-A paper tranche (`paper/research2/`) is built from.
The originals live outside this repository, under the Research2 evidence
root (`%LOCALAPPDATA%\Argine3\research`, `store.evidence_root()`), which is
local machine state and is never tracked by Git.

These copies exist so that the paper tree (figures, tables, claim ledger,
freeze manifest) is reproducible from the Git repository alone, without
requiring access to the original machine's local evidence root. They are
**not** the scientific source of truth — the canonical run directories
named in `paper/research2/II-A-FREEZE.json` are. Nothing here is modified
after copying; `scripts/verify_data_snapshot.py` can re-hash these files
against the original evidence root to confirm they still match, when run
on a machine that has that root.

| Run                               | Experiment | Outcome | run_id                              |
|------------------------------------|------------|---------|--------------------------------------|
| R2-01 (trace/replay validation)    | R2-01      | PASS    | `bb6d8ca34dbc4537a43d54be30c9f3e1`   |
| R2-02 (Oracle vs retention @4GiB)  | R2-02      | PASS    | `238c8ee7f0914df7b28942ec99627c24`   |
| R2-03 (budget-sensitivity sweep)   | R2-03      | PASS    | `d957077073374f1e9fd1e101a002946f`   |
| R2-04 (recoverable-margin synthesis)| R2-04     | PASS    | `cd2b58b019314fafb90304234e6d1907`   |

Validated source trace SHA-256:
`e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`
