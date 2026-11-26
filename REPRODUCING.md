# Reproducing Argine

This is the single reproduction guide for the consolidated repository (Research 1-4). It first explains the structure and how provenance works, then how to run each verifier; the preserved Research 1 toolchain guide follows as Part II.

## Part I — structure, provenance and verifiers

### Structure

The frozen scientific tree is physically unchanged from the research repositories: `experiments/` (EXP-*, R2-*, R3-*, R4-*), `paper/`, `data/`, `argine/`, `tools/`, `tests/`. The folders [`research/`](research/README.md) and [`papers/`](papers/README.md) are an index layer on top of it; [`manifests/research-layout-map.json`](manifests/research-layout-map.json) maps every path to its research. Nothing was moved because frozen records and verifiers store `experiments/...` paths and hash the files.

### Frozen stages

Each closed stage carries a `FREEZE.json` (file SHA-256 values, contract digests, freeze identifiers). Frozen files are byte-identical to the originals: [`manifests/tracked-file-hashes.json`](manifests/tracked-file-hashes.json) records SHA-256 before and after consolidation (all equal). [`.gitattributes`](.gitattributes) pins per-file line endings so that a clone on any operating system reproduces the original on-disk bytes (several verifiers hash on-disk bytes). One file, `experiments/R3-02B-acquisition-driver/README.md`, had mixed line endings in the original and is checked out with CRLF only; no verifier depends on it.

### Omitted raw artifacts

Four R2-12 conformance traces (~1.9 GB each) and the external R4 trace corpora are not in Git. Their path, size and SHA-256 are in [`manifests/raw-artifacts-omitted.json`](manifests/raw-artifacts-omitted.json) and (R4) `experiments/R4-00-causal-eviction-distillation/DATA_SPLITS.json`; the Research 1 external raw inventory is `data/external_raw_manifest.csv`. The preservation location of the omitted traces beyond the author's local repositories is UNKNOWN. A hash identifies an artifact: if you obtain a file, its SHA-256 must equal the recorded value, otherwise it is not the evidence.

### How hashes establish provenance

Verifiers recompute SHA-256 of the committed artifacts and compare with the frozen records, so any silent edit to a frozen file, result or digest makes a verifier fail. Some frozen verifiers also asked Git about the historical commits at which stages were frozen (`git log -- PATH`, `git show COMMIT:PATH`, `git diff --name-only REV -- PATHS`). Those commits contain the oversized traces and are not in this repository's public history. [`manifests/git-object-equivalence.json`](manifests/git-object-equivalence.json) records, for those queries, the original Git object IDs; the wrapper [`shared/tooling/run_frozen_verifier.py`](shared/tooling/run_frozen_verifier.py) (via [`shared/tooling/gitcompat/`](shared/tooling/gitcompat/sitecustomize.py)) answers them only if the current blob equals the recorded original, and otherwise fails like a missing object. All other Git commands pass through. Details: [`manifests/consolidation-path-changes.json`](manifests/consolidation-path-changes.json). Earlier repositories are preserved as tags `legacy/argine2/*`, `legacy/argine3/*`, `legacy/argine4/*` ([`manifests/legacy-repositories.json`](manifests/legacy-repositories.json)).

### Running each verifier (from the repository root)

All of this needs Python 3.9+ and no model weights or network.

| scope | command | needs |
|---|---|---|
| everything below | `python shared/tooling/verify_all.py` | as below |
| Research 1 registry | `python tools/index_experiments.py --check` | nothing external |
| Research 1/2 unit tests | `python -m unittest discover -s tests` | some tests need a built runtime or raw data and are environment-dependent |
| Research 2 paper freeze | `python paper/research2/scripts/verify_paper_freeze.py` | nothing external |
| Research 3 closure | `python shared/tooling/run_frozen_verifier.py experiments/R3-final-closure/closure.py verify` | nothing external |
| Research 3 paper | `python shared/tooling/run_frozen_verifier.py paper/research3/scripts/verify_paper.py` | nothing external |
| Research 4 paper | `python shared/tooling/run_frozen_verifier.py paper/research4/scripts/verify_paper.py` | nothing external (check-only; `--write` regenerates the source map) |
| Research 4 Phase 0 | `python shared/tooling/run_frozen_verifier.py experiments/R4-00-causal-eviction-distillation/phase0.py verify` | verifies frozen records; regenerating from raw traces needs the external corpus |
| Research 4 Phase 1 | `python shared/tooling/run_frozen_verifier.py experiments/R4-01-cfdc-training-validation/phase1.py verify` | frozen records |
| Research 4 Phase 2 | `python shared/tooling/run_frozen_verifier.py experiments/R4-02-cfdc-prospective-eval/phase2.py verify` | frozen records |
| Research 5 Phase 0 | `python shared/tooling/run_frozen_verifier.py experiments/R5-00-exposed-transfer-stall/phase0.py verify` | frozen protocol only; nothing external |
| Research 5 Phase 1a | `python shared/tooling/run_frozen_verifier.py experiments/R5-01-ets-implementation/phase1a.py verify` | implementation record and gate; the C++ conformance test runs when g++ is available |
| Research 5 G1 seal | `python shared/tooling/run_frozen_verifier.py experiments/R5-01-g1-host-evidence/g1_seal.py verify` | sealed host evidence copies (raw host files checked only with `--host-dir`) |
| Research 5 Phase 1b readiness | `python shared/tooling/run_frozen_verifier.py experiments/R5-02-headroom-gate/phase1b.py verify` | protocol, sources and G1 seal; no outcome artifact may exist |
| Research 5 G2 seal | `python shared/tooling/run_frozen_verifier.py experiments/R5-02-g2-host-evidence/g2_seal.py verify` | sealed G2 evidence and exact H (raw host files with `--host-dir`, trace re-derivation with `--rederive`) |
| Research 5 Phase 2 readiness | `python shared/tooling/run_frozen_verifier.py experiments/R5-03-prospective-evaluation/phase2.py verify` | protocol, sources, Phase 0, G1 and G2 seals; no opening or outcome artifact may exist |
| documentation | `python tools/check_links.py` and `python strumenti/controlla_readme.py` | nothing external |
| consolidation | `python shared/tooling/verify_consolidation.py` | Git |

Stages that need external assets: re-scoring R4 from raw traces (external trace corpora), re-processing Research 1 raw runs (`data/external_raw_manifest.csv`), re-checking R2-12 raw traces (omitted files), and any new model run (GGUF models, the patched llama.cpp runtime, local hardware; Part II.6).

---

## Part II — Research 1 toolchain and replication (preserved)

## Part II.1 Requirements

The analysis and verification path requires:

- Python 3.9 or newer;
- the Python dependencies listed in `requirements.txt`;
- Git for repository-integrity checks.

From the repository root:

```bash
python -m pip install -r requirements.txt
```

The repository can be used directly without installing the package. On Windows,
`argine.bat` invokes the code from the current checkout.

## 2. Verify the research artifact without a model

Run:

```bash
python tools/index_experiments.py --check
python tools/paper_figures.py --check
python -m unittest discover -s tests
```

These checks do not require model weights or network access.

Historical experiment tables can be regenerated from the committed Argine v1
raw logs with:

```bash
python tools/build_historical_data.py
```

The generated historical outputs are deterministic across supported platforms.

## 3. Regenerate the paper figures

The six main figures used by the paper are generated from committed experiment
data:

```bash
python tools/paper_figures.py
```

Use:

```bash
python tools/paper_figures.py --check
```

to validate the source data and figure invariants without rewriting the figure
files.

## 4. Raw and processed evidence

The main evidence locations are:

- `data/raw/argine-v1/` — imported Argine v1 execution records;
- `data/raw/legacy/` — legacy routing evidence;
- `data/processed/` — derived legacy analyses;
- `experiments/EXP-*/raw/` — external raw records for individual experiments; these are intentionally excluded from Git and cryptographically inventoried by `data/external_raw_manifest.csv` and `data/external_raw_summary.json`;
- `experiments/EXP-*/processed/` — derived experiment outputs;
- `experiments/EXP-*/results.csv` — experiment-level result tables;
- `experiments/INDEX.md` — experiment registry and final status.

Experiment conclusions are versioned with committed processed evidence and protocol metadata. The larger external raw corpus is bound to this publication candidate by per-file SHA-256 digests in `data/external_raw_manifest.csv`. Historical
conclusions are not silently rewritten when later experiments supersede or
qualify them.

## 5. Research command interface

The research command family is available with:

```bat
argine.bat research --help
```

Important commands include:

```text
trace        instrumented end-to-end runs
convert      legacy/raw log to router trace
characterize routing statistics per layer
replay       replay a policy under a hard memory budget
compare      compare policies across budgets
elasticity   per-layer memory elasticity
oracle       future-informed and static reference analyses
adaptive     adaptive residency controller and ablations
analyze      combined trace analysis
reprocess    regenerate a run from its raw runtime log
fidelity     runtime-versus-replay validation
iotimeline   queue-depth and latency reconstruction
predict      performance-model fitting and validation
report       experiment report generation
fingerprint  runtime/model/workload provenance
schedule     interleaved experimental schedules
```

Every research command writes its results to files; terminal output is not the
sole copy of an experimental result.

## 6. Experimental replication with models

Model weights are not distributed with this repository.

The evaluated runtime is based on a pinned llama.cpp revision plus the patches
in `patches/`. Build instructions are given in the main `README.md`.

On the reference Windows workflow, a model run begins with a configured research
runtime, for example:

```bat
set ARGINE_LLAMA_DIR=%CD%\llama-research
argine.bat doctor
```

An expert store can then be built for a locally supplied GGUF model:

```bat
argine.bat store build C:\path\to\model.gguf
```

Instrumented experiments use commands such as:

```bat
argine.bat research trace C:\path\to\model.gguf --expert-store --budgets 4G,2G,1.5G --repeat 3 --workload coding --split dev --out experiments\SMOKE-example
```

Machine-local paths such as `C:\modelli\...` preserved in frozen experiment
configuration files are provenance from the reference machine. Replicators must
substitute their own local model and runtime paths.

## 7. Reproduction versus replication

The repository supports two distinct claims:

**Artifact reproduction** means checking and regenerating the repository-contained
derived outputs from committed evidence and verifying the internal consistency of the research artifact. Full raw reprocessing additionally requires the external corpus matching the committed SHA-256 manifest.

**Experimental replication** means collecting new measurements under a
compatible protocol. New performance measurements on other hardware extend the
evidence; they do not replace the reference-machine measurements.

Tokens/s, I/O latency, queue depth, and other wall-clock quantities can depend on
hardware, operating system, runtime build, background load, and storage state.
For this reason, the repository records fingerprints and experimental protocol
metadata rather than treating identical performance across machines as a
reproducibility requirement.

## 8. Paper

The paper source is in `paper/`.

Its figures are produced by `tools/paper_figures.py` from the committed
experimental evidence. The paper should be interpreted together with the
experiment registry and the individual `EXP-*` records, which contain the
scope, protocol, result, and limitations behind each reported claim.

## Public snapshot and historical provenance

The public Argine2 release is distributed from a clean repository snapshot rather than from the complete pre-publication Git history. Historical Argine2 commit identifiers embedded in frozen experiment protocols, configuration files, processed reports, and experiment narratives are intentionally retained as immutable provenance identifiers. They refer to the original research-development history and are not guaranteed to resolve as commits in the public snapshot history. External upstream commit identifiers, such as the pinned llama.cpp revision, retain their normal upstream meaning. The frozen experiment records themselves are part of the released artifact and are not rewritten to manufacture a new public lineage.
