# R2-10 Phase 3A — EVAL acquisition execution plan (for human execution)

**Status update (this follow-up):** the schema-compatibility blocker
documented in the original version of this file is now RESOLVED (see
`argine/research/workloads.py`'s additive `items`-schema fallback branch,
covered by `tests/test_r2_09_eval_workload_schema.py`, 19/19 passing). The
only remaining blocker is host-side: this session's own shell sandbox
exposes only ~3.8 GiB RAM, below the capture tool's own 8G floor and far
below the real ~16 GB machine. Real capture must run directly on that
machine, by a human, using the commands below.

The model file and the runtime binary were both found on this machine and
SHA-256-verified byte-for-byte against the frozen Research2 values, and the
real `argine research trace` CLI (confirmed live via `--help` and a real
`--dry-run` invocation that loaded the actual EVAL workload file and
reached the runtime's own memory planner) is the right, and now fully
working, tool. No second inference engine was introduced.

## Step 0 — loader fix (DONE, nothing further required)

`argine/research/workloads.py`'s `load()` now recognizes two schemas:

- `{"splits": {"dev": [...], "eval": [...]}}` (every existing workload
  file in `workloads/v1/`) — completely unchanged behavior.
- `{"items": [...]}` under the file's own top-level `"split"` field (the
  four frozen `research2/workloads/r2_09/*-v1.json` EVAL files) — a new,
  additive branch that only triggers when `"splits"` is absent.

The four frozen files were NOT modified (SHA-256 verified identical before
and after this change; see `PHASE3A-EVAL-ACQUISITION-PLAN.json`). Live
end-to-end verification (`python -m argine research trace ... --dry-run`)
confirmed the real CLI now loads `coding-eval-v1` (`sha256 a7af97847483`)
and reaches the real memory planner without error.

## Step 1 — pre-flight hash/identity checks (run once, before any capture)

Run from the repository root (`C:\Users\alpel\Desktop\Argine3`) in
PowerShell. These must match exactly before proceeding; if either
mismatches, STOP — do not capture.

```powershell
Get-FileHash "C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf" -Algorithm SHA256
# must equal a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32

Get-FileHash "..\Argine2\llama-research-v2\llama-server.exe" -Algorithm SHA256
# must equal a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3

# Confirm the machine itself has enough headroom before launching (close
# YouTube / unnecessary Chrome tabs per the project's own hardware
# guidance first):
Get-Counter '\Memory\Available MBytes'
# should read comfortably above 8192 (8G); this machine is ~16 GB total.
```

## Step 2 — run each of the 4 captures, in order

Seeds are fixed prospectively in `PHASE3A-SEED-PLAN.json` (seed=42 for all
four cells, following the repository's own established primary-capture
convention — see that file for the full rationale). Do not change a seed
after looking at any capture's output.

Each command below fails closed on its own if: the output directory
already contains a `run-00-4G` subdirectory (PowerShell `Test-Path` guard
included), the estimated memory plan exceeds the 4G expert budget (the
planner itself refuses to start, as already observed live in this
session), or available RAM is below the tool's 8G floor (printed as a
warning; treat it as a stop signal here, don't override it with
`--min-start-available 0`).

### 1) coding-eval-v1

```powershell
$out = "$env:LOCALAPPDATA\Argine3\research\R2-09-EVAL\captures\coding-eval-v1"
if (Test-Path $out) { throw "output already exists: $out" }
python -m argine research trace "C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf" `
  --expert-store --expert-budget 4G --full-checksum `
  --workload "research2\workloads\r2_09\coding-v1.json" --split eval --seed 42 `
  --threads 4 --llama-arg=--threads-batch --llama-arg 12 --ctx 4096 `
  --out $out
```

### 2) reasoning-eval-v1

```powershell
$out = "$env:LOCALAPPDATA\Argine3\research\R2-09-EVAL\captures\reasoning-eval-v1"
if (Test-Path $out) { throw "output already exists: $out" }
python -m argine research trace "C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf" `
  --expert-store --expert-budget 4G --full-checksum `
  --workload "research2\workloads\r2_09\reasoning-v1.json" --split eval --seed 42 `
  --threads 4 --llama-arg=--threads-batch --llama-arg 12 --ctx 4096 `
  --out $out
```

### 3) summarization-eval-v1

```powershell
$out = "$env:LOCALAPPDATA\Argine3\research\R2-09-EVAL\captures\summarization-eval-v1"
if (Test-Path $out) { throw "output already exists: $out" }
python -m argine research trace "C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf" `
  --expert-store --expert-budget 4G --full-checksum `
  --workload "research2\workloads\r2_09\summarization-v1.json" --split eval --seed 42 `
  --threads 4 --llama-arg=--threads-batch --llama-arg 12 --ctx 4096 `
  --out $out
```

### 4) instruction-eval-v1

```powershell
$out = "$env:LOCALAPPDATA\Argine3\research\R2-09-EVAL\captures\instruction-eval-v1"
if (Test-Path $out) { throw "output already exists: $out" }
python -m argine research trace "C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf" `
  --expert-store --expert-budget 4G --full-checksum `
  --workload "research2\workloads\r2_09\instruction-v1.json" --split eval --seed 42 `
  --threads 4 --llama-arg=--threads-batch --llama-arg 12 --ctx 4096 `
  --out $out
```

Run them one at a time (not concurrently — they share the RAM budget);
let each finish before starting the next. There is no historical
EVAL-length (512-generated-token, 4G expert budget, CPU-only) timing to
cite, so budget generously.

## Step 3 — post-capture validation (run after EACH capture, before moving on)

```powershell
# from the repository root, in the Argine3 Python environment
python -c "from argine.research2.r2_01.trace_contract import normalize, reconcile; r = normalize(r'%LOCALAPPDATA%\Argine3\research\R2-09-EVAL\captures\coding-eval-v1\run-00-4G\trace.jsonl.gz', source_experiment='R2-09B-EVAL'); print(reconcile(r)); print(r.source.sha256)"
```

Repeat the same `normalize`/`reconcile` call for the other 3 captures,
pointing at their own `run-00-4G\trace.jsonl.gz`. `reconcile(r)` must
return an empty list (no exclusions/row-loss) for the cell to count as a
clean capture; per `eval_manifest.json`'s own `rejection_retry_rules`, a
reject gets at most one retry with the identical frozen spec, and if the
retry differs, record both and mark the cell INCONCLUSIVE pending review
rather than discarding either.

Record, for each of the 4 traces: its path, its SHA-256 (printed above),
the `reconcile()` result, and the capture's own `manifest.json` (written
automatically into each output directory by `argine research trace`).

## Step 4 — hand back to this session

Once all 4 traces are captured and validated clean, hand the 4 trace
paths + SHA-256 values back to a Phase-3A-successor session so it can seal
them to `EVAL_CAPTURED_SEALED` (an additive provenance record, same as
`PHASE3A-EVAL-ACQUISITION-PLAN.json`'s `EVAL_ACQUISITION_PLANNED` record —
`eval_manifest.json` itself is never edited). That session still must NOT
transition to `EVAL_OPENED` or compute any EVAL performance metric; that
remains a separately authorized future step.
