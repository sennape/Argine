# AUDIT-ORACLE-001 — Research2 Oracle Objective and Evaluation Contract

**Status:** complete (standalone audit). **Final verdict:** `BLOCKED_FOR_SCIENTIFIC_AMENDMENT`.

This audit is a hard gate before Research II-C / R2-09. It does **not** start R2-09, does **not** implement a predictor, does **not** run the 30B model, does **not** generate canonical traces, does **not** modify canonical experiment evidence, and does **not** modify `paper/research2/`.

## Central question

Are the Research II-A "Oracle-Bounded" miss-byte claims mathematically justified for the actual implemented comparator/objective/cache semantics?

## Costs and ordering (required)

Notation:

- `B`: miss-byte cost of a selected causal baseline under fixed workload, byte budget, and cache semantics.
- `O*`: true minimum miss-byte cost achievable by an unrestricted offline/clairvoyant policy under exactly the same workload, byte capacity, request grouping, admission, and cache semantics.
- `C*`: infimum/minimum miss-byte cost achievable by the admissible class of causal policies under the same conditions.
- `O_B`: miss-byte cost of the implemented `Oracle-Global-Belady` comparator (`argine.research.oracle.BeladyGlobal`, furthest-next-use Belady-style policy).

By definition: `O* <= C*` and `O* <= O_B`. Unless `O_B` is proven optimal, there is **no general ordering** between `O_B` and `C*`.

```text
              O*
             /  \
            /    \
          O_B    C*
            ??????????
         no guaranteed
            ordering

        (all <= B conceptually, for a fixed baseline/cost context)
```

Explanation:

- `O* <= O_B`: the implemented clairvoyant comparator cannot beat the true offline optimum.
- `O* <= C*`: no causal policy can beat the true offline optimum.
- No general ordering exists between `O_B` and `C*` unless additional optimality results are established. Therefore `B - O_B` (MeasuredBeladyGap) is **neither** a proven upper bound nor a proven lower bound on true maximum causal recovery `B - C*` in general. It is `<= B - O*` (gap to true offline optimum is at least the measured comparator gap relative to `O* <= O_B`), but that does not rescue causal-recovery bound claims.

`C*` is not computed here and should not be treated as default future compute work. For an unrestricted/broad causal-policy class, `C*` may be `NOT_IDENTIFIABLE_WITHOUT_POLICY_CLASS_RESTRICTION`; that is structural, not a temporary audit deficiency.

## Key findings

- **Expert sizes (R2 replay/Oracle cache object = `(layer,expert)`):** 6144 objects (48 layers × 128 experts), two distinct per-layer byte sizes (2,654,208 and 3,059,712), 24 layers each; equal within layer, variable across layers. `ARE_ALL_CACHEABLE_EXPERT_OBJECTS_EXACTLY_EQUAL_SIZED = false`. CV ~0.071, max/min ~1.153 are descriptive diagnostics only (near-uniform by those measures), **not** CASE A/A' evidence without certified epsilon.
- **Oracle algorithm:** `BeladyGlobal` / `Oracle-Global-Belady` is **O2** — classical furthest-next-use (Belady-style) under variable object sizes, multi-key event protection, byte capacity, next-use from full future event list. Object size does **not** participate in eviction scoring. Implementation states it is not proven optimal when sizes differ.
- **Optimality:** miss-count `NOT_PROVEN_OPTIMAL`; miss-byte `NOT_PROVEN_OPTIMAL`.
- **Case:** **CASE C — BYTE OPTIMALITY NOT ESTABLISHED**. CASE A' not applicable (no certified epsilon). CASE B not established.
- **Sensitivity:** `NO_COUNTEREXAMPLE_FOUND` (two offline clairvoyant heuristics; one tied canonical O_B miss bytes, one worse). Not evidence of optimality.
- **True offline byte optimum `O*`:** `POSSIBLE_BUT_EXPENSIVE` (not computed; no in-repo certified solver/bound validated for exact R2 semantics).
- **Title:** `TITLE_INVALID_UNDER_CURRENT_OBJECTIVE` ("Oracle-Bounded" is a mathematical claim not established for the primary miss-byte objective). Final editorial title choice belongs to the project owner.
- **EXP-025:** `P2` (stream-dependent beyond aggregate bytes: reads, `qd`/bandwidth, decode attribution); equal total miss bytes can produce materially different modeled time (**YES**). Transferability to arbitrary policy-induced miss streams: **UNSUPPORTED**.
- **DEV/EVAL:** R2-01/R2-08 Research2 artifacts inspected are documented as `dev` splits where stated; no II-C predictor/baseline-tournament tuning occurred (II-C not started). Held-out Research2 EVAL for II-C is not established; `eval_evidence_available=false` for that purpose.
- **R2-09:** **BLOCKED** pending scientific/paper amendment and preregistered evaluation contract.

## Artifacts

| File | Purpose |
|---|---|
| `README.md` | This summary and required cost/ordering diagram |
| `expert_sizes.csv` | Cacheable `(layer,expert)` object sizes from canonical trace meta |
| `expert_size_summary.json` | Size distribution and exact-equality verdict |
| `oracle_algorithm.md` | `BeladyGlobal` algorithm/semantics audit and classification |
| `oracle_objective_verdict.json` | Oracle/objective/case verdicts |
| `byte_aware_sensitivity.md` | Sensitivity check report |
| `byte_aware_sensitivity.json` | Machine-readable sensitivity results |
| `byte_aware_sensitivity_check.py` | Deterministic offline sensitivity script |
| `byte_optimum_feasibility.md` | `O*` feasibility assessment |
| `performance_model_audit.md` | EXP-025 domain/class/transferability audit |
| `performance_model_verdict.json` | EXP-025 verdict |
| `dev_eval_inventory.csv` | Documented role / actual usage / leakage matrix |
| `leakage_assessment.md` | Leakage-safe contract requirements |
| `claim_impact_map.md` | Manuscript/ledger/freeze claim impact (no paper edits made) |
| `downstream_r2_09_impact.md` | R2-09 implications and prerequisites |
| `audit_summary.json` | Schema-compliant machine-readable audit summary |

## Reproducibility

- Expert-size extraction: deterministic read of canonical trace meta (read-only).
- Byte-aware sensitivity: `python audits/AUDIT-ORACLE-001/byte_aware_sensitivity_check.py` (offline, deterministic given same code/trace; imports `argine.research`; no model/network/trace mutation).
- Canonical evidence referenced by path/run ID/digest/SHA; no large traces or model files copied.

## Final verdict

`BLOCKED_FOR_SCIENTIFIC_AMENDMENT` — CASE C (byte optimality not established). R2-09 remains blocked until scientific semantics are amended. Scientific correctness has priority over historical "Oracle-Bounded" framing.
