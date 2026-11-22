# Research Part 2 — automation

```
argine.bat research2 status
argine.bat research2 doctor
argine.bat research2 next
argine.bat research2 run R2-01
argine.bat research2 analyse R2-01
argine.bat research2 audit
```

(`argine.bat` is a four-line wrapper: `cd /d "%~dp0" && python -m argine
%*`; every command above is equally `python -m argine research2 ...` from
the Argine3 repository root.)

- **`status`** — cheap, read-only. Baseline SHA, current SHA/branch,
  working-tree clean/dirty, current phase, every experiment's state, the
  next eligible experiment and whether it's implemented/runnable, and the
  evidence root in use. Never hashes the model, never starts the runtime,
  never modifies repository or scientific state.
- **`doctor`** — read-only environment/readiness diagnostic (OS, Python,
  repo root, Git, branch, Git cleanliness informationally, evidence-root
  writeability, roadmap/protocol validity). Never starts the model, never
  does heavy artifact hashing.
- **`next`** — the next eligible experiment from the roadmap + local state.
  In R2-M0.1 this resolves to `R2-01`, correctly reported as not yet
  implemented.
- **`run <ID>`** — the generic experiment-run contract. Validates the ID
  (fails closed on an unknown one), fails closed on a dirty tree unless
  `--allow-dirty` is passed, then either dispatches to a registered
  implementation (`EXPERIMENT_RUN_IMPLEMENTATIONS` in
  `argine/research2/runner.py` — empty in R2-M0.1) or returns
  `NOT_IMPLEMENTED` and creates no run directory, no provenance file, no
  false evidence.
- **`analyse <ID>`** — the generic offline-analysis contract: regenerate
  summaries/plots/statistics from existing raw evidence without rerunning
  the model. `NOT_IMPLEMENTED` for every experiment in R2-M0.1.
- **`audit`** — validates roadmap schema, phase ordering, experiment ID
  uniqueness, the baseline identity record, expected directory structure,
  evidence-root configuration, the provenance schema, registry integrity,
  and stored-state validity. Never starts the model.

## Recommended workflow

```
argine.bat research2 status
```
then, once an experiment's implementation has been approved and lands in
`EXPERIMENT_RUN_IMPLEMENTATIONS`:
```
argine.bat research2 next
argine.bat research2 run <ID>
```
No manual orchestration of Python modules, CSV inspection, or
result-directory creation should be required for ordinary use.

## Extending the framework

A future experiment implementation plugs into `run_experiment()` /
`analyse_experiment()` by registering a callable in
`EXPERIMENT_RUN_IMPLEMENTATIONS` / `EXPERIMENT_ANALYSE_IMPLEMENTATIONS`
(`argine/research2/runner.py`), not by replacing `status`/`doctor`/`next`/
`audit` or the CLI wiring. The framework may calculate statistics; it must
not autonomously decide that a hypothesis is scientifically established.
