# R3-02C — Lossless Trace Storage Resolution

Status: **LOSSLESS_ARCHIVE_STORAGE_SAFE** — lossless application-level archival of the identical
logical JSONL makes the frozen acquisition fit on this host without changing any scientific content,
row, counter, identity, matrix or endpoint.

No model was loaded, no preflight and no acquisition ran, and no frozen historical trace was
modified or deleted.

## Measured compression (1.79 GiB conformance trace, temporary copy)

| method | ratio | compressed | compress | decompress | SHA equal | parser equal |
|---|---|---|---|---|---|---|
| gzip-6 | 0.2186 | 419,938,680 B | 46.1 s | 5.1 s | yes | yes |
| gzip-9 | 0.2135 | 410,136,252 B | 291.0 s | 7.3 s | yes | yes |
| zlib-9 | 0.2135 | 410,136,224 B | 313.6 s | 7.3 s | yes | yes |
| **zstd-3** | **0.0111** | **21,262,440 B** | **1.6 s** | **1.5 s** | **yes** | **yes** |
| zstd-19 | 0.0062 | 11,922,396 B | 106.9 s | 1.5 s | yes | yes |

Chosen: **zstd-3** (`zstandard 0.24.0`, already present in the frozen host interpreter) — best
speed/ratio trade-off; decompression restores the byte-identical JSONL and the frozen
`extract_decision_trace` returns identical counters for every method.

## Scientific equivalence

- `SHA256(logical JSONL) == SHA256(decompressed archive)` for every method.
- Frozen parser output identical: 9,504 decision rows, 9,504 completion rows, miss bytes
  30,100,414,464, peak resident 4,294,950,912, completeness and budget invariant unchanged.

## Identity semantics

- **Logical trace identity** remains `telemetry.trace_sha256` = SHA-256 of the uncompressed JSONL
  bytes — the scientific identity, independent of storage encoding.
- The archive adds `telemetry.archive_storage`: `format`, `archive_sha256` (SHA-256 of the archive
  file), `archive_bytes`, `logical_sha256`, `logical_bytes`. `telemetry.archive.path` points to the
  archive and keeps `sha256`/`bytes` as the logical values.

Classification: **STORAGE_REPRESENTATION_ONLY** — the archive is a lossless storage envelope; no
contract row, counter, rule, endpoint, matrix or policy is changed. If a future contract revision
requires the on-disk artifact to be plain JSONL, this becomes CONTRACT_VERSION_REQUIRED; the
representation is then re-expressed as an additive v2 without any scientific change.

## Peak storage model (measured ratio, sequential lifecycle)

Lifecycle per TRACE_COUNT: raw JSONL written → validate completeness/admission → compress →
verify logical SHA + parser counters + archive SHA → persist metadata atomically → delete raw only
after verification → CELLs reference the logical identity.

| scenario | raw peak | prior archives | temp archive | peak | +15 GiB reserve |
|---|---|---|---|---|---|
| expected | 28.5 GiB (8 GiB trace) | ≈1.1 GiB | 0.32 GiB | **29.9 GiB** | 44.9 GiB |
| conservative | 31.4 GiB | ≈1.2 GiB | 0.35 GiB | **32.9 GiB** | **47.9 GiB** |

Current free space: **64.94 GiB** (69,726,781,440 B). Required: **47.9 GiB** → **LOSSLESS_ARCHIVE_STORAGE_SAFE**.

## Driver

- TRACE_COUNT: raw written by the runtime, validated, admitted, then archived; the archive is
  verified (logical SHA, logical bytes, parser counters, archive SHA) before the raw file is
  deleted; metadata and archive are written atomically; resume states `RAW / ARCHIVING / ARCHIVED /
  SUCCEEDED`; corrupted or partial archives are rejected and the raw trace is retained.
- Per-TRACE_COUNT storage gate: free space must exceed `15 GiB reserve + 110 % logical trace +
  temp archive + prior archives`, otherwise the launch fails closed before starting.
- The 15 GiB system reserve is part of the requirement, not a warning.

## Tests

72 tests pass (58 prior + 14 archive/peak guards): round-trip SHA and parser equality, corrupted and
partial archive rejection, raw retained on verification failure, raw removed only after verified
archive, resume after interrupted compression, separate logical/archive SHA recording, no duplicate
raw trace, CELL references the logical identity, storage requirement includes the reserve, gate uses
the conservative requirement, free-space detection, no process started.
