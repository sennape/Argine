# Research 5 — claim boundaries

- Only `R5_POSITIVE_RUNTIME_IMPROVEMENT` may be described as an improvement of the Argine runtime. Every other label is a negative or uninterpretable outcome. None may be called a partial success, a promising result or a mechanism win.
- A positive result is bounded to the following setting. It does not establish generality across models, hardware, runtimes, workloads or budgets.
  - model: Qwen3-30B-A3B-Q4_K_M;
  - runtime: the frozen R5 build of the expert-store runtime;
  - host: one Windows laptop (i5-1340P, NVMe, 16 GB);
  - prompts: the four evaluation prompts;
  - budget: 1.5 GiB, with a no-harm check at 4 GiB.
- The oracle bound H is an optimistic characterisation of headroom. It is never an achievable or achieved gain.
- Fewer misses, fewer transfers, a positive overlap fraction or a traced-arm difference are not runtime improvements on their own.
- A negative result closes the tested deferred-wait scheduler in the tested regime. It does not show that latency hiding, I/O scheduling or other runtime changes cannot help.
- Research 5 does not revisit Research 2–4 conclusions, which stay as frozen. It does not claim that retention is impossible.
- The non-hook time per token is reported in every case. If C hides waits but slows compute, for example because concurrent DMA contends for memory, that is the expected explanation of a mechanism-without-gain outcome. It is not an anomaly to explain away.
