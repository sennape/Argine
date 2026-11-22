"""R3-03A Part I: collapse frozen R3 gate/up/down triplets into semantic events and
compare with the R2 event definition and the frozen R2 reference predictor.

Offline, no model. Reads a frozen R3-02 B-trace (zstd) through `zstd -dc`
(argument list, no shell). Does NOT read any R3-02G Session 1 file.

    python replay_frozen_triplets.py <B-4GiB.zst> <out.json>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import r3_event_semantics_audit as aud  # noqa: E402
from argine.research.policies import Context  # noqa: E402
from argine.research2.r2_bridge01.retention_policies import PredictedProtectPolicy  # noqa: E402

TOP_K = 8
N_LAYERS = 48


def collapse(rows):
    """One semantic event per (pass, layer): the first row of each same-layer group.

    Structural (not set-equality based): groups are consecutive same-layer rows, passes
    advance when the layer index does not increase. Returns (events, anomalies).
    """
    groups = aud.build_layer_groups(rows)
    events, anomalies = [], []
    for gi, g in enumerate(groups):
        n = len(g["rows"])
        if n != 3:
            anomalies.append({"group": gi, "pass": g["pass"], "layer": g["layer"], "rows": n})
        first = g["rows"][0]
        events.append({"pass": g["pass"], "layer": g["layer"], "experts": list(first["requested"]),
                       "protected_logged": list(first.get("protected", [])),
                       "identical_triplet": all(r["requested"] == first["requested"] for r in g["rows"])})
    return events, anomalies


def r2_reference_protected(events, n_experts=128):
    """Frozen R2 PredictedProtectPolicy driven one access() per semantic event."""
    layers = sorted({e["layer"] for e in events})
    ctx = Context(layers=layers, n_experts=n_experts, size={l: 1 for l in layers},
                  budget=10 ** 15, top_k=TOP_K)
    pol = PredictedProtectPolicy(0.99, 64)
    pol.start(ctx)
    out = []
    for step, e in enumerate(events):
        out.append(sorted(pol._predicted_next.get(e["layer"], frozenset())))
        pol.access(step, e["layer"], e["experts"])
    return out


def main(src: str, dst: str) -> None:
    p = subprocess.Popen(["zstd", "-dc", src], stdout=subprocess.PIPE)
    rows = list(aud.extract_rows(p.stdout))
    if p.wait() != 0:
        raise SystemExit("zstd failed")
    events, anomalies = collapse(rows)
    passes = max(e["pass"] for e in events) + 1
    per_pass_layers = {}
    for e in events:
        per_pass_layers.setdefault(e["pass"], []).append(e["layer"])
    structurally_ok = all(v == list(range(N_LAYERS)) for v in per_pass_layers.values())
    decode_pass = [p_ for p_, v in per_pass_layers.items()
                   if all(len(e["experts"]) == TOP_K for e in events if e["pass"] == p_)]
    groups_all = aud.build_layer_groups(rows)
    dec_set = set(decode_pass)
    ref = r2_reference_protected(events)
    sims, last_pred, sim_sets = {}, {}, []
    for e in events:
        sim_sets.append(sorted(last_pred.get(e["layer"], ())))
        last_pred[e["layer"]] = sims.setdefault(e["layer"], aud.DecayedTransitionSim()).observe(e["experts"])
    same_sim = sum(1 for a, b in zip(ref, sim_sets) if a == b)
    same_logged = sum(1 for r_, e in zip(ref, events) if r_ == sorted(e["protected_logged"]))
    first_div = next((i for i, (a, b) in enumerate(zip(ref, sim_sets)) if a != b), None)
    first_div_logged = next(({"event_index": i, "pass": events[i]["pass"], "layer": events[i]["layer"]}
                             for i, (r_, e) in enumerate(zip(ref, events)) if r_ != sorted(e["protected_logged"])), None)
    out = {
        "artifact": "R3-03A-FROZEN-TRIPLET-REPLAY",
        "source": Path(src).name,
        "original_physical_rows": len(rows),
        "corrected_semantic_events": len(events),
        "compression_ratio": len(rows) / len(events),
        "forward_passes": passes,
        "decode_passes": len(decode_pass),
        "events_per_decode_token_corrected": (len([e for e in events if e["pass"] in set(decode_pass)]) / max(1, len(decode_pass))),
        "original_rows_per_decode_token": sum(len(g["rows"]) for g in groups_all if g["pass"] in dec_set) / max(1, len(decode_pass)),
        "r2_event_count_expected": passes * N_LAYERS,
        "structure": {"every_pass_has_layers_0_to_47_once": structurally_ok,
                      "groups_not_of_3_rows": len(anomalies), "anomaly_examples": anomalies[:5],
                      "all_triplets_identical_sets": all(e["identical_triplet"] for e in events)},
        "r2_reference_vs_corrected_stream": {
            "frozen_reference": "argine.research2.r2_bridge01.retention_policies.PredictedProtectPolicy",
            "events_compared": len(events), "protected_sets_identical_to_cpp_mirror_sim": same_sim,
            "first_divergence_event_index": first_div},
        "r2_reference_vs_logged_original_protected_before": {
            "events_compared": len(events), "identical": same_logged,
            "fraction_identical": same_logged / len(events), "first_divergence": first_div_logged,
            "meaning": "logged protected_before came from the 3x-advanced predictor; the corrected runtime must instead equal the R2 reference"},
    }
    Path(dst).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("original_physical_rows", "corrected_semantic_events", "compression_ratio",
                                          "forward_passes", "r2_event_count_expected")}))
    print(json.dumps(out["structure"]), json.dumps(out["r2_reference_vs_corrected_stream"]),
          json.dumps(out["r2_reference_vs_logged_original_protected_before"]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
