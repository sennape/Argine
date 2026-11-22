# Contributing to Argine3

Argine is a research project on memory-constrained Mixture-of-Experts inference. This repository, **Argine3**, contains the project through the completion of **Research 2** (Research 1 is CLOSED; Research 2 is CLOSED / FROZEN). The most valuable contributions produce evidence — including evidence that something does not work.

## Scope of this repository

- Frozen scientific evidence (experiment records, `*-FREEZE.json` files, result JSON, classification labels, measured numbers, raw-data manifests and the frozen papers) **must not be modified**.
- New scientific work is **additive**: it adds new records and never edits or reinterprets old ones. Research after Research 2 belongs to later repository generations, not to this repository; contributions here are replications, audits, corrections recorded as new records, and documentation.
- Protocol and decision rules are frozen **before** evaluation, wherever the experiment has an evaluation.
- Scientific history is not rewritten: no history rewriting, no amended or force-pushed commits on published history.
- Reader-facing scientific names and canonical repository identifiers (`EXP-NNN`, `R2-NN`, classification labels) are distinct things: identifiers are permanent and stay in provenance material; prose uses descriptive names.
- Every claim maps to evidence: name the experiment or milestone and the artifact that supports it.
- Validators and tests stay green (see rule 9 below, and `python tools/check_links.py` and `python strumenti/controlla_readme.py` for documentation).
- Large raw artifacts that are omitted from Git remain represented through hashes and manifests.
- Documentation-only changes must not alter any scientific classification, number or label.

## Good contributions

- **Reproduce an existing experiment** (`EXP-NNN` or a Research 2 milestone) and report whether you get the same result.
- **Add a workload** to `workloads/v1/` (or propose `v2`), with disjoint `dev` and `eval` prompts.
- **Test an open hypothesis** (`RQ-NNN` in [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md)).
- **Implement a baseline policy** in `argine/research/policies.py` (ARC, LeCaR, a published MoE policy) — with a test that it never exceeds its budget.
- **Improve instrumentation** (`patches/argine-research.*`) — with a test in `argine-research-test.cpp`.
- **Analyse raw traces** already in `data/` or `experiments/*/raw/`.
- **Reproduce a result on other hardware**, clearly labelled as a different machine (it extends, it does not replace, the reference results).
- **Disprove an interpretation.** If a README paragraph says more than its data, say so with the data.

## Rules that keep results comparable

1. **Cite IDs.** Every result names the `RQ-` it addresses and the `EXP-` it belongs to. New experiments use the next free `EXP-NNN-short-name` identifier. IDs are never reused or renamed.
2. **Raw data first.** Commit raw outputs (`raw.tsv`, `trace.jsonl.gz`, CSV/JSON). Screenshots and Markdown tables are not data.
3. **Fingerprint.** Runs made with `argine research trace` carry `manifest.json`; keep it. A result without a complete fingerprint is not publication-quality (`argine research fingerprint` tells you what is missing).
4. **Repeat and interleave.** At least 3 runs (5 preferred) per configuration, configurations interleaved inside one session (`argine research schedule --configs A,B,C --repeat 3`).
5. **Separate tuning from evaluation.** Tune on `dev`, claim on `eval`. Label in-sample results as such.
6. **Tag every statement** with `[MEASURED]`, `[DERIVED]`, `[HYPOTHESIS]`, `[SUPPORTED]`, `[FALSIFIED]`, `[OPEN]` or `[LIMITATION]`.
7. **Never rewrite old conclusions.** Mark them `SUPERSEDED by EXP-NNN` and add the new one.
8. **Keep the hard memory invariant.** No policy may exceed its budget, ever; replay fails loudly if one does.
9. Run `python -m unittest discover -s tests` and `python tools/index_experiments.py --check` before submitting.

## Contribution template

Use this in the pull request or issue body (also in `.github/ISSUE_TEMPLATE/research-contribution.md`):

```text
Related research question:
RQ-XXX

Related experiment:
EXP-XXX (or: new, EXP-0NN-short-name)

Hypothesis:
...

Method:
(model, quantisation, budget(s), workload + split, binary fingerprint, repetitions, order)

Result:
...

Does this:
SUPPORT / FALSIFY / EXTEND / REPLICATE

Raw data:
(paths in the repository)
```

A negative result is acceptable and welcome.

## Code style

Pure Python standard library in `argine/research` (numpy and matplotlib are optional accelerators / figure makers, never required for a result). Every module documents the definitions it implements next to the code. C++ follows the existing patches: self-contained, testable without llama.cpp, clean under ThreadSanitizer and AddressSanitizer.
