# Literature boundary — Causal Future-Distance Distillation (CFDC)

Checked 2026-11-22 against fetched pages. Items marked *unverified* could not be confirmed from a fetched page and are recorded only so they can be checked later.

## Closest prior work

**Bacon, Calanzone, Larouche (2026), "Fewer Expert Transfers Without Changing Routing: Expert Eviction for MoE Offloading", arXiv:2611.15842.** Title and authors verified (arXiv PDF; listed as a poster at the NeurIPS 2026 AXIOM workshop). Submission date and version: *unverified*. Their setup: OLMoE-1B-7B, L40S GPU, uniform 12.6 MB bf16 experts, half the MoE layers offloaded, metric = steady-state expert copies per token; the reported percentages match (LRU − policy) / (LRU − farthest-future), with LRU as reference. Reported findings checked against the paper:

- farthest-future eviction reduces copies 2.1× without changing routing — confirmed;
- 16 tokens of future demand recover 96% of the farthest-future reduction — confirmed;
- a history-only exponentially decayed count rule recovers 26.9% — confirmed; the decay value is not stated;
- a learned causal scorer (past use + router features) reaches 32–33% on 4 held-out sequences; with past-use features only, or over all 16 sequences, learned and hand-built rules tie at about 25–27% — partly confirmed, with that caveat;
- closing the causal-versus-future gap is stated as open — confirmed.

**FlashMoE (Kim, Lee, Han, Yoo, Kim, arXiv:2601.17063, January 2026).** A small feed-forward network on recency/frequency features, trained on Belady labels, evicts experts; evaluated on OLMoE and **Qwen3-30B-A3B**; reports hit-rate gain over LRU, not a headroom fraction. This is the most direct overlap in model and method.

**Zhang (2026), "Reproducible Evaluation of MoE Expert Caching: Replay Semantics, Workload Contamination, and Operating Regimes", arXiv:2608.07911.** Qwen3-30B-A3B on MLX; warns that the offline-optimal gap overstates what causal methods can recover, and that replay semantics and the oracle definition change conclusions.

## Related areas

- Local routing consistency: Liang, Wang, Tian, Li, Tang, Wei, "Not All Models Suit Expert Offloading: On Local Routing Consistency of Mixture-of-Expert Models", arXiv:2505.16056 (v1 May 2025, v4 February 2026; ICLR 2026 venue *unverified*).
- Belady-supervised cache replacement: Hawkeye (Jain and Lin, "Back to the Future: Leveraging Belady's Algorithm for Improved Cache Replacement"; ISCA 2016 venue *unverified* from the fetched text); Glider (Shi, Huang, Jain, Lin, "Applying Deep Learning to the Cache Replacement Problem", MICRO-52, 2019) — distils an offline model into a simple online predictor; Parrot (Liu, Hashemi, Swersky, Ranganathan, Ahn, "An Imitation Learning Approach for Cache Replacement", arXiv:2006.16239; ICML 2020 *unverified*) — trains on the learner's own cache states (DAgger); LRB (Song, Berger, Li, Lloyd, "Learning Relaxed Belady for Content Distribution Network Caching", NSDI 2020).
- MoE expert caching/offloading systems: MoE-Infinity (arXiv:2401.14361), ProMoE (arXiv:2410.22134), ExpertFlow (arXiv:2410.17954), MoE-CORE (arXiv:2610.01950, hand-weighted eviction score; date *unverified*).

## What this study does not claim

No novelty is claimed for: using Belady/farthest-future as an oracle; learning an eviction policy from future labels (Hawkeye, Glider, Parrot, LRB, FlashMoE, Bacon et al.); training under the learner's own states; recency/frequency/decayed-count features (Bacon et al., MoE-CORE); decision trees or distilling a cheap rule from a richer model (Glider); or the observation that causal rules capture only a minority of the headroom (Bacon et al., Zhang).

## The narrower contribution under test

A prospectively frozen test, in the already-characterized Argine regime (Qwen3-30B-A3B-Q4_K_M routing traces, llama.cpp byte-budgeted expert store at 1.5 GiB, demand admission, event-atomic multi-key events, two expert byte sizes), of how much of the frozen Belady **miss-byte** headroom an extremely cheap causal rule (one regression tree, depth ≤ 3, ≤ 4 integer features, integer comparisons) recovers when evaluated under its own cache state — with a strict no-rescue decision procedure, comparators already known to reach roughly a quarter of the headroom in the literature (decayed count) or tested in Research 3 (NUE_X), and an explicit economic transfer to any later runtime work (κ_low = 0.3792 ms/MB, 10 ms/token floor, benefit/cost ≥ 2).

Because expert sizes here differ only slightly (about 2.65 and 3.06 MB), a byte-weighted headroom is not claimed to be a separate contribution from transfer counts.

The oracle is fixed up front: BeladyGlobal without bypass, reference = b_selected (not LRU), pooled miss bytes. Results are therefore not directly comparable with LRU-referenced percentages in Bacon et al. or with best-causal-referenced gaps in Zhang.
