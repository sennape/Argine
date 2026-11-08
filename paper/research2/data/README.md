# Frozen Research2 evidence snapshot (II-A + II-B)

This directory holds exact, byte-for-byte copies of the specific canonical
evidence files the Research2 paper (`paper/research2/`) is built from for
the frozen II-A and II-B tranches. The originals live outside this
repository, under the Research2 evidence root
(`%LOCALAPPDATA%\Argine3\research`, `store.evidence_root()`), which is
local machine state and is never tracked by Git.

These copies exist so that the paper tree (figures, tables, claim ledger,
freeze manifests) is reproducible from the Git repository alone, without
requiring access to the original machine's local evidence root. They are
**not** the scientific source of truth — the canonical run directories and
digests named in `paper/research2/II-A-FREEZE.json` and
`paper/research2/II-B-FREEZE.json` are. Nothing here is modified after
copying.

## II-A (frozen)

| Run                                | Experiment | Outcome | run_id                              |
|------------------------------------|------------|---------|--------------------------------------|
| R2-01 (trace/replay validation)    | R2-01      | PASS    | `bb6d8ca34dbc4537a43d54be30c9f3e1`   |
| R2-02 (Oracle vs retention @4GiB)  | R2-02      | PASS    | `238c8ee7f0914df7b28942ec99627c24`   |
| R2-03 (budget-sensitivity sweep)   | R2-03      | PASS    | `d957077073374f1e9fd1e101a002946f`   |
| R2-04 (comparator-gap decomposition; historical R2-04 run name retained) | R2-04 | PASS | `cd2b58b019314fafb90304234e6d1907` |

Validated source trace SHA-256 (II-A and R2-05--R2-07; reused by R2-08 coding-v1/seed-42):
`e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`

## II-B (frozen)

| Run                                   | Experiment | Outcome | run_id                              | Canonical digest (full) |
|---------------------------------------|------------|---------|--------------------------------------|-------------------------|
| R2-05 (frequency/concentration)       | R2-05      | PASS    | `da82e15b61c94019840477642618565a`   | `4a1b72cdfcd37144446fc1a106b6fa8d559d10bc0729e1baf16dda7c1fa620df` |
| R2-06 (transition structure)          | R2-06      | PASS    | `2e075161f02443ff822279add6938ca4`   | `a727edf42735286420d326baad481687167d3d0aba0444a527183c084140926e` |
| R2-07 (reuse distance/working set)    | R2-07      | PASS    | `af28e8c6dc5044a18e4a86137d59156a`   | `ad36e615970b196b64ba3a3fdc2559e252cc8627e5d17d0d830d16742f59281a` |
| R2-08 (drift/workload dependence)     | R2-08      | PASS    | `6165471a12574e05b48932aac2f1626c`   | `f5db4e0bf43ce4243edb303441e82ceefa5a0aeb5754143292f1230f220527c3` |

R2-08 canonical model SHA-256: `a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32`

R2-08 canonical runtime SHA-256: `a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3`

R2-08 capture-ID audit: seven R2-08-managed new capture cells have `capture_run_id`/`capture_source` null in the canonical capture matrix, while retaining trace path, trace SHA-256, workload/seed/config/model/runtime identities, validation state, and canonical R2-08 run binding. This is documented as an intentional representation for R2-08-internal captures; no capture IDs were invented and no scientific traces were modified.
