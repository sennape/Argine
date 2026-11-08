# R2-08 — Routing Drift and Workload Dependence (Research Part 2 / II-B)

This directory mirrors the canonical experiment entry in
`experiments/R2-08-routing-drift/README.md`.

- **Implementation:** `argine/research2/r2_08/`
- **Prerequisites:** R2-01/R2-04/R2-05/R2-06/R2-07 PASS (II-A closed/paper-frozen)
- **Current state:** IMPLEMENTED / **READY FOR REAL ANALYSIS** (no real 30B capture executed during implementation; no canonical result yet)
- **Model scope:** Qwen3-30B-A3B-Q4_K_M only; no predictor, no retention-policy change, no performance claim, no cross-model claim
- **Human command:**
  ```bat
  argine.bat research2 run R2-08 --model C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf
  ```
- **After PASS:** II-B CLOSED/PASS; next action is II-B paper freeze (not R2-09). `paper/research2/` unchanged.
