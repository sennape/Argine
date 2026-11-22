"""R2-04 orchestration: the full workflow behind `argine research2 run
R2-04` / `argine research2 analyse R2-04`.

A pure offline SYNTHESIS over R2-01/R2-02/R2-03's already-completed
evidence: R2-01 PASS + R2-02 PASS + R2-03 PASS + binding consistency
(prerequisite.py) -> load R2-03's own registered budget_results.json
dynamically (never hardcoded) -> decompose every budget into STRUCTURAL /
INFORMATION / TOTAL gaps (decomposition.py) -> classify regimes, peak
opportunity, curve shape, headroom persistence (regimes.py) -> secondary
LFU summary (lfu_summary.py) -> paper-ready claims, explicitly separated
into SUPPORTED NOW vs NOT YET SUPPORTED (claims.py) -> figure data
(figures.py) -> scientific outcome classification -> evidence written
under the Research2 evidence root -> registry/state update -> human-
readable summary.

No model weights are loaded, no runtime binary is invoked, no new trace
capture or replay happens anywhere in this module -- R2-04 reads only
already-registered R2-01/R2-02/R2-03 evidence files. Determinism is
verified by running the synthesis step TWICE against the identical
loaded upstream data and comparing a canonical digest (volatile run ids/
timestamps excluded) -- a mismatch is an immediate FAIL, mirroring the
double-replay determinism checks R2-02/R2-03 perform on live replay.
"""

from __future__ import annotations

import hashlib
import io
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import provenance, store
from ..r2_02 import policy_set
from ..r2_03 import budget_sweep
from . import claims as claims_mod
from . import decomposition as decomp
from . import figures as figures_mod
from . import lfu_summary
from . import prerequisite
from . import regimes

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_PREREQUISITE_NOT_MET = "R2_01_R2_02_OR_R2_03_PREREQUISITE_NOT_MET"

LABEL_SHARDED = policy_set.LABEL_RUNTIME_SHARDED_LRU
LABEL_GLOBAL_LRU = policy_set.LABEL_GLOBAL_LRU
LABEL_ORACLE = policy_set.ORACLE_LABEL


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_r2_03_budget_results(r2_03_run_dir: Path) -> Dict[int, Dict[str, Dict[str, Any]]]:
    data = json.loads((r2_03_run_dir / "budget_results.json").read_text(encoding="utf-8"))
    return {int(budget): {row["policy"]: row for row in rows} for budget, rows in data.items()}


def _canonical_digest(payload: Dict[str, Any]) -> str:
    """sha256 of the canonical (sorted-key, no-whitespace-ambiguity) JSON
    encoding. Caller is responsible for excluding volatile fields (run
    ids, timestamps) from `payload` before calling this."""
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class SynthesisError(ValueError):
    """Raised when the synthesis step itself discovers an internally
    impossible or malformed upstream result (section 18 FAIL conditions)."""


def _synthesize(budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                fixed_budgets_bytes: List[int],
                reproduction_budget_bytes: Optional[int]) -> Dict[str, Any]:
    """The deterministic synthesis step: pure function of
    (budget_results, fixed_budgets_bytes, reproduction_budget_bytes).
    Called twice by run() with identical inputs as a determinism check."""
    for budget, rows in budget_results.items():
        for policy_label, row in rows.items():
            if row["hits"] + row["misses"] != row["total_expert_requests"]:
                raise SynthesisError(f"budget {budget}/{policy_label}: malformed "
                                     f"upstream row -- hits + misses != "
                                     f"total_expert_requests")

    try:
        decomposition_by_budget = decomp.decompose_all_budgets(budget_results)
    except decomp.ImpossibleValuesError as e:
        raise SynthesisError(f"decomposition produced internally impossible values: {e}")

    missing_budgets = sorted(set(fixed_budgets_bytes) - set(decomposition_by_budget))

    cap_constrained = regimes.capacity_constrained_budgets(budget_results, LABEL_GLOBAL_LRU)
    eff_residency = regimes.effective_residency_budget(budget_results, LABEL_GLOBAL_LRU, LABEL_ORACLE)
    peak = regimes.peak_opportunity(decomposition_by_budget)
    persistence = regimes.headroom_persistence(decomposition_by_budget, cap_constrained, eff_residency)

    primary_curve = figures_mod.figure_b_information_headroom_fraction_by_budget(decomposition_by_budget)
    curve_shape = regimes.classify_curve_shape(primary_curve, eff_residency)

    lfu_rows = lfu_summary.lfu_vs_global_lru(budget_results, LABEL_GLOBAL_LRU)

    fig_a = figures_mod.figure_a_miss_rate_by_budget(budget_results, LABEL_GLOBAL_LRU, LABEL_ORACLE)
    fig_c = figures_mod.figure_c_structural_vs_informational_absolute_gap_by_budget(decomposition_by_budget)

    claim_set = claims_mod.build_claims(decomposition_by_budget, reproduction_budget_bytes,
                                        eff_residency, curve_shape["classification"])
    claims_mod.check_no_forbidden_claims(claim_set)

    summary_metrics = _summary_metrics(decomposition_by_budget, reproduction_budget_bytes,
                                       eff_residency, peak, persistence)

    return {
        "missing_budgets": missing_budgets,
        "decomposition_by_budget": decomposition_by_budget,
        "capacity_constrained_budgets": cap_constrained,
        "effective_residency_budget": eff_residency,
        "peak_opportunity": peak,
        "headroom_persistence": persistence,
        "primary_curve_information_headroom_fraction_vs_budget": primary_curve,
        "curve_shape": curve_shape,
        "lfu_vs_global_lru": lfu_rows,
        "figure_a_miss_rate_by_budget": fig_a,
        "figure_b_information_headroom_fraction_by_budget": primary_curve,
        "figure_c_structural_vs_informational_absolute_gap_by_budget": fig_c,
        "claims": claim_set,
        "summary_metrics": summary_metrics,
    }


def _summary_metrics(decomposition_by_budget: Dict[int, Dict[str, Any]],
                     reproduction_budget_bytes: Optional[int],
                     effective_residency_budget: Optional[int],
                     peak: Dict[str, Any],
                     persistence: Dict[str, Any]) -> Dict[str, Any]:
    """Section 13's small, paper-relevant summary-metrics set."""
    repro = decomposition_by_budget.get(reproduction_budget_bytes) if reproduction_budget_bytes else None
    return {
        "r2_01_replay_fidelity_status": "PASS",
        "reproduction_budget_bytes": reproduction_budget_bytes,
        "information_headroom_fraction_at_reproduction_budget": (
            repro[decomp.KEY_INFORMATION]["relative_miss_byte_reduction"] if repro else None),
        "max_tested_information_headroom_fraction": peak["max_relative_miss_byte_headroom"],
        "max_absolute_miss_byte_headroom": peak["max_absolute_miss_byte_headroom"],
        "first_tested_policy_indifference_budget_bytes": effective_residency_budget,
        "structural_gap_at_reproduction_budget": (
            repro[decomp.KEY_STRUCTURAL] if repro else None),
        "information_gap_at_reproduction_budget": (
            repro[decomp.KEY_INFORMATION] if repro else None),
        "oracle_advantage_persists_at_every_capacity_constrained_point": (
            persistence["oracle_strictly_improves_at_every_capacity_constrained_point"]),
        "causal_recovery": "NOT_YET_MEASURED -- II-C",
    }


def run(*, allow_dirty: bool = False, **_ignored: Any) -> Dict[str, Any]:
    rec = provenance.build_record("R2-04", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_r2_01_r2_02_r2_03_pass()
    if not binding.ok:
        return {
            "experiment_id": "R2-04", "run_id": run_id,
            "outcome": OUTCOME_PREREQUISITE_NOT_MET,
            "message": binding.message,
            "prerequisite_status": binding.status,
        }

    try:
        budget_results = _load_r2_03_budget_results(Path(binding.r2_03_run_dir))
    except (OSError, json.JSONDecodeError, KeyError) as e:
        return {
            "experiment_id": "R2-04", "run_id": run_id, "outcome": OUTCOME_FAIL,
            "message": f"could not read R2-03's own registered budget_results.json: {e}",
            "r2_03_run_dir": binding.r2_03_run_dir,
        }

    synthesis_errors: List[str] = []
    try:
        synth_a = _synthesize(budget_results, binding.fixed_budgets_bytes,
                              binding.reproduction_budget_bytes)
        synth_b = _synthesize(budget_results, binding.fixed_budgets_bytes,
                              binding.reproduction_budget_bytes)
    except SynthesisError as e:
        synthesis_errors.append(str(e))
        synth_a = synth_b = None
    except claims_mod.ForbiddenClaimError as e:
        synthesis_errors.append(f"generated claim violated the naming safeguard: {e}")
        synth_a = synth_b = None

    nondeterminism: List[str] = []
    digest_a = digest_b = None
    if synth_a is not None and synth_b is not None:
        digest_a = _canonical_digest(synth_a)
        digest_b = _canonical_digest(synth_b)
        if digest_a != digest_b:
            nondeterminism.append(
                f"two independent syntheses over identical upstream evidence produced "
                f"different canonical digests ({digest_a} != {digest_b})")

    outcome, reasons = classify_outcome(binding, budget_results, synthesis_errors,
                                        nondeterminism, synth_a)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["parameters"] = {
        "r2_01_run_id": binding.r2_01_run_id, "r2_02_run_id": binding.r2_02_run_id,
        "r2_03_run_id": binding.r2_03_run_id,
        "fixed_budgets_bytes": binding.fixed_budgets_bytes,
        "reproduction_budget_bytes": binding.reproduction_budget_bytes,
    }
    rec["artifacts"]["model"] = None

    run_dir = store.run_dir("R2-04", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
    })

    synth = synth_a or {}
    _write_json(run_dir / "recoverable_margin.json", {
        "decomposition_by_budget": {str(b): d for b, d in
                                    synth.get("decomposition_by_budget", {}).items()},
        "capacity_constrained_budgets": synth.get("capacity_constrained_budgets", []),
        "effective_residency_budget": synth.get("effective_residency_budget"),
        "peak_opportunity": synth.get("peak_opportunity", {}),
        "headroom_persistence": synth.get("headroom_persistence", {}),
        "curve_shape": synth.get("curve_shape", {}),
        "lfu_vs_global_lru": synth.get("lfu_vs_global_lru", []),
        "figure_a_miss_rate_by_budget": synth.get("figure_a_miss_rate_by_budget", []),
        "figure_b_information_headroom_fraction_by_budget": synth.get(
            "figure_b_information_headroom_fraction_by_budget", []),
        "figure_c_structural_vs_informational_absolute_gap_by_budget": synth.get(
            "figure_c_structural_vs_informational_absolute_gap_by_budget", []),
    })
    if synth.get("claims") is not None:
        _write_json(run_dir / "claims.json", synth["claims"])
    if synth.get("decomposition_by_budget"):
        _write_text(run_dir / "margin_by_budget.csv", _to_csv(synth["decomposition_by_budget"]))

    summary = {
        "experiment_id": "R2-04", "run_id": run_id, "outcome": outcome,
        "scope": "offline SYNTHESIS of already-completed R2-01/R2-02/R2-03 evidence "
                "into a structural/information/total miss-cost decomposition and an "
                "upper-bound (never demonstrated-causal) recoverable-margin analysis "
                "-- no new replay, no model/runtime invocation, no new trace capture",
        "reasons": reasons,
        "candidate_status": rec["git"]["candidate_status"],
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "summary_metrics": synth.get("summary_metrics", {}),
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "ii_a_scientific_state": {
            "r2_01": "CLOSED/PASS", "r2_02": "CLOSED/PASS", "r2_03": "CLOSED/PASS",
            "r2_04": outcome,
            "ii_a_closed": outcome == OUTCOME_PASS,
            "ii_b": "FUTURE/READY (not auto-started by this run; paper-freeze is the "
                   "next project action)",
            "ii_c": "FUTURE",
        },
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary, synth))

    store.append_registry_entry({
        "identity": rec["identity"], "run_dir": str(run_dir),
    })
    new_state = "COMPLETED" if outcome == OUTCOME_PASS else (
        "INCONCLUSIVE" if outcome == OUTCOME_INCONCLUSIVE else "FAILED")
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-04", step_state)
    store.set_experiment_state("R2-04", new_state)

    return {**summary, "run_dir": str(run_dir)}


def classify_outcome(binding: Any, budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                     synthesis_errors: List[str], nondeterminism: List[str],
                     synth: Optional[Dict[str, Any]]) -> "tuple[str, List[str]]":
    """Section 18's outcome rules, applied mechanically."""
    reasons: List[str] = []

    if synthesis_errors:
        return OUTCOME_FAIL, list(synthesis_errors)
    if nondeterminism:
        return OUTCOME_FAIL, list(nondeterminism)
    if synth is None:
        return OUTCOME_FAIL, ["synthesis produced no result for an unspecified reason"]

    if synth["missing_budgets"]:
        return OUTCOME_INCONCLUSIVE, [
            f"budgets {synth['missing_budgets']} are declared in R2-03's fixed budget "
            f"sweep but do not have all three required policy rows present in its "
            f"registered evidence -- a clean decomposition cannot be produced for "
            f"them"]

    if not budget_results:
        return OUTCOME_INCONCLUSIVE, ["R2-03's registered budget_results.json contains "
                                     "no budgets at all"]

    reasons.append(
        "R2-01 PASS, R2-02 PASS, and R2-03 PASS canonical evidence verified; trace "
        "identity and binding consistent across all three; R2-03's own R2-02-"
        "reproduction check reads OK; structural/information/total decompositions "
        "computed deterministically (verified by two independent syntheses producing "
        "an identical canonical digest) for every declared budget; no internally "
        "impossible decomposition; regime classification, peak-opportunity maxima, "
        "and curve-shape classification produced; causal recovery explicitly left "
        "NOT_YET_MEASURED.")
    return OUTCOME_PASS, reasons


def _to_csv(decomposition_by_budget: Dict[int, Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["budget_bytes", "structural_miss_reduction", "structural_miss_byte_reduction",
              "structural_relative_miss_byte_reduction", "information_miss_reduction",
              "information_miss_byte_reduction", "information_relative_miss_byte_reduction",
              "total_miss_reduction", "total_miss_byte_reduction",
              "total_relative_miss_byte_reduction"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for budget in sorted(decomposition_by_budget):
        d = decomposition_by_budget[budget]
        s, i, t = d[decomp.KEY_STRUCTURAL], d[decomp.KEY_INFORMATION], d[decomp.KEY_TOTAL]
        writer.writerow({
            "budget_bytes": budget,
            "structural_miss_reduction": s["miss_reduction"],
            "structural_miss_byte_reduction": s["miss_byte_reduction"],
            "structural_relative_miss_byte_reduction": s["relative_miss_byte_reduction"],
            "information_miss_reduction": i["miss_reduction"],
            "information_miss_byte_reduction": i["miss_byte_reduction"],
            "information_relative_miss_byte_reduction": i["relative_miss_byte_reduction"],
            "total_miss_reduction": t["miss_reduction"],
            "total_miss_byte_reduction": t["miss_byte_reduction"],
            "total_relative_miss_byte_reduction": t["relative_miss_byte_reduction"],
        })
    return buf.getvalue()


def _summary_markdown(summary: Dict[str, Any], synth: Dict[str, Any]) -> str:
    lines = [
        f"# R2-04 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Summary metrics", ""]
    for k, v in summary.get("summary_metrics", {}).items():
        lines.append(f"- **{k}**: {v}")
    lines += ["", "## Curve shape", "",
             f"- {synth.get('curve_shape', {}).get('classification')}: "
             f"{synth.get('curve_shape', {}).get('reason')}"]
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-04"]
    if not registry:
        return {"experiment_id": "R2-04", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-04 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-04 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-04", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-04 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-04 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
