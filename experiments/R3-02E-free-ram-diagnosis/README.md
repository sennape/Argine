# R3-02E — 8 GiB Free-RAM Feasibility Gate Diagnosis

Result: **HOST_RAM_GENUINELY_INSUFFICIENT** (marginal, ~0.35 GiB at the observed free RAM).
No code change, no gate weakening.

## Why the gate refused at 10.19 GiB free

`argine.planner.safe_budget()` uses the *live* available RAM when it is below the
theoretical auto budget (total − 25 % reserve = 11.79 GiB), and keeps a 512 MiB
margin for the OS:

    budget = available − 512 MiB = 10.19 − 0.50 = 9.69 GiB

The frozen 8 GiB request estimates:

| component | GiB |
|---|---|
| shared tensors resident | 0.999 |
| expert cache (8 GiB budget) | 8.000 |
| repack (shared only) | 0.224 |
| KV cache (4096, f16) | 0.375 |
| activations | 0.069 |
| runtime fixed | 0.375 |
| **total** | **10.042** |

10.042 > 9.69 → infeasible by **0.352 GiB**. The planner needs
**available ≥ 10.542 GiB** at the moment it runs; 10.60 GiB is accepted.

## No double counting

The estimate uses `weights_resident = shared_bytes` (0.999 GiB) and
`expert_cache = expert_store_budget` (8.0 GiB) — never the whole model plus the
store. The measured A-8GiB resident peak (8,589,901,824 B) matches the cache term.

## Completed A-8GiB evidence

A-8GiB completed on this host (512 tokens, 74,016 decision rows, 0 budget
violations, peak 8.0 GiB, ADMISSIBLE). It proves the configuration is runnable
here when more RAM is free; it does not prove 10.19 GiB suffices, and no RSS
telemetry exists. It does not contradict the planner.

## Operator action

Free at least **10.6 GiB** available at launch time (recommend ≥ 11.5 GiB):
close the DSH window and browsers, stop background services, then run the
scientific command from a plain terminal. No protocol, matrix, evidence or gate
change is required.
