# R2-10 Phase 3A — EVAL acquisition execution plan (for human execution)

This session (device_bash) cannot safely run the real capture: its own
shell sandbox exposes only 3.8 GiB RAM, and the frozen R2-09 EVAL workload
spec files are not yet in the schema the existing loader expects. Both are
reported in `PHASE3A-ACQUISITION-READINESS-AUDIT.json`. Everything else
checks out: the model file and the runtime binary were both found on this
machine and SHA-256-verified byte-for-byte against the frozen Research2
values, and the real `argine research trace` CLI (confirmed live via
`--help`/`--dry-run`, not just read from source) is the right tool.

## Step 0 — one-time loader fix (required before any of the 4 captures)

`argine/research/workloads.py`'s `load()` expects
`{"splits": {"eval": [...]}}`; the frozen
`research2/workloads/r2_09/{coding,reasoning,summarization,instruction}-v1.json`
files use `{"items": [...]}` instead (confirmed live: loading one of them
today returns zero prompts, not an error). Resolve this with **either**:

- **(a)** a small loader change: in `load()`, when `d.get("items")` is
  present and `d.get("splits")` is not, treat it as
  `{"splits": {d["split"]: d["items"]}}` before building `prompts`. This
  changes no frozen contract file -- `workloads.py` is generic tooling,
  not a Phase-0 `COMPONENT_FILES` member -- but it is still a code change
  to shared infrastructure and should go through the same review/test
  discipline as any other fix (e.g. the Phase-2 performance fix), not be
  slipped in silently.
- **(b)** a non-frozen adapter script that reads each frozen file and
  writes a byte-identical-prompt, `splits`-schema copy to a scratch path
  (e.g. `research2/workloads/r2_09/_cli_adapted/coding-v1.json`), leaving
  the frozen originals and their SHA-256 untouched, and pointing
  `--workload` at the adapted copy instead.

Either way: do not edit the frozen files themselves (their SHA-256 is
pinned in `eval_manifest.json`), and verify after the fix that
`argine.research.workloads.load(path).split("eval")` returns exactly 1
prompt matching the frozen file's `items[0]`, for all 4 specs.

## Step 1 — run each of the 4 captures (PowerShell / cmd, on the real machine)

One command per held-out instance. Run from the repository root
(`C:\Users\alpel\Desktop\Argine3`), with the loader fix from Step 0 in
place first:

```bat
python -m argine research trace "C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf" ^
  --expert-store --expert-budget 4G ^
  --workload "research2\workloads\r2_09\coding-v1.json" --split eval --seed 42 ^
  --threads 4 --llama-arg=--threads-batch --llama-arg 12 --ctx 4096 ^
  --out "%LOCALAPPDATA%\Argine3\research\R2-09-EVAL\captures\coding-eval-v1"
```

Repeat with `reasoning-v1.json` / `summarization-v1.json` / `instruction-v1.json`
in place of `coding-v1.json`, and the matching output directory suffix
(`reasoning-eval-v1`, `summarization-eval-v1`, `instruction-eval-v1`). Each
run uses the SAME runtime (resolved automatically by
`argine.research2.runtime_resolver`; no `--runtime` override needed on this
machine) and the SAME model/budget/ctx/thread settings -- only the
workload and output directory change, exactly as
`dev_eval_protocol.json`'s "held constant" list requires.

**Before starting:** close YouTube and any unnecessary Chrome tabs/apps
(per the project's own hardware guidance), since this is a real, CPU-only
(no GPU backend available on this machine), potentially long-running
30B-parameter inference under a 4 GiB expert-memory budget. Expect each
of the 4 captures (512 generated tokens each) to take a non-trivial amount
of wall-clock time; there is no historical EVAL-length timing data to cite
precisely, so budget generously and let each one finish before starting
the next (the tool's own `--min-start-available` floor of 8G will warn,
not block, if available RAM is lower than ideal at launch time).

## Step 2 — verify each resulting trace, then hand back to Phase 3A

For each of the 4 output directories, record:
- the trace file's path and SHA-256,
- the capture's reported model/runtime/workload/configuration fingerprints,
- `targets_scored`-equivalent sanity (decode steps > 0, routing cardinality
  == top_k == 8 at every observed step, per
  CARDINALITY-AVAILABILITY-AUDIT-001's method).

Hand the 4 trace paths + SHA-256 values back for Phase 3A to validate and
seal to `EVAL_CAPTURED_SEALED` (this session can do that validation/sealing
step once real trace files exist -- it is the actual inference that this
session's own sandbox cannot run).
