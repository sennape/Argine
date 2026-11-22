"""Validator for R3-04 OPENING_FREEZE.json (model-free). Recomputes every binding from the committed artifacts; exits non-zero on any mismatch.
  python validate_opening.py            validates experiments/R3-04-net-runtime-value/OPENING_FREEZE.json
"""
from __future__ import annotations

import json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import r3_04_acquire as A  # noqa: E402
import r3_04_host_identity as HI  # noqa: E402

V = A.V


def validate(path: Path = None) -> dict:
    path = path or A.OPENING_FREEZE
    of = json.loads(path.read_text(encoding="utf-8"))
    assert V.freeze_sha(of) == of["freeze_sha256"], "opening freeze sha"
    pf = A.load_protocol_freeze(of["protocol_freeze_sha256"])
    am, sched = A.load_amendment(of["amendment_freeze_sha256"], pf["freeze_sha256"])
    g2 = json.loads((A.GATE2_DIR / "FREEZE.json").read_text()); assert g2["freeze_sha256"] == of["gate2_freeze_sha256"] and g2["classification"] == "R3_04_MECHANISM_PASS"
    assert g2["shutdown_helper_status"] == "R3_04_SHUTDOWN_HELPER_VALIDATED_ON_REAL_MODEL"
    assert V.sha_lf(A.TOOLS / "r3_04_acquire.py") == of["driver_sha256_lf"], "driver differs from the frozen driver"
    assert of["runtime_sha256"] == V.RUNTIME_SHA256 and of["amended_schedule_sha256"] == V.schedule_sha(sched)
    assert (of["physical_launches"], of["counted_launches"], of["warmups"]) == (146, 144, 2) == (len(sched), sum(s["kind"] == "counted" for s in sched), sum(s["kind"] == "warmup" for s in sched))
    assert of["blocks"] == {"1.5": 18, "8": 6} and of["primary_n_blocks"] == V.BUDGETS["1.5"]["n_blocks"] == 18 and not any(s["budget"] == "4" for s in sched)
    plan = json.loads(HI.PLAN.read_text())
    for k in ("model", "store"):
        assert of[k]["sha256"] == plan[k]["sha256"] and of[k]["size_bytes"] == plan[k]["bytes"] and of[k]["path"] == plan[k]["path"], k
    assert of["shutdown_method_required"] == "own_console_helper" and of["clean_environment_attestation_required"] is True and of["fixed_n"] is True and of["optional_stopping"] is False
    assert of["campaign_authorized"] is False and of["confidence_level"] == 0.95 == 1 - V.ALPHA
    return {"ok": True, "freeze_sha256": of["freeze_sha256"]}


if __name__ == "__main__":
    print(validate())
