"""Independent validator of the R3-04 campaign-efficiency amendment: recomputes schedule, hours and invariants from the frozen protocol; verifies the freeze. No model, no network."""
from __future__ import annotations

import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1] / "tools"))
import time_model as T  # noqa: E402
import r3_04_acquire as A  # noqa: E402

V = T.V
PROTO = HERE.parent / "R3-04-net-runtime-value"


def validate() -> dict:
    fz = json.loads((HERE / "FREEZE.json").read_text()); am = json.loads((HERE / "AMENDMENT.json").read_text()); pf = json.loads((PROTO / "FREEZE.json").read_text())
    assert V.freeze_sha(fz) == fz["freeze_sha256"], "amendment freeze sha"
    for n, h in fz["files_sha256_lf"].items():
        assert V.sha_lf(HERE / n) == h, n
    assert V.freeze_sha(pf) == pf["freeze_sha256"] == am["parents"]["protocol_freeze_sha256"], "protocol freeze unchanged / parent"
    for n, h in pf["files_sha256_lf"].items():
        assert V.sha_lf(PROTO / n) == h, f"protocol file {n} changed"
    g2 = json.loads((PROTO / "gate2-mechanism-evidence" / "FREEZE.json").read_text()); assert g2["freeze_sha256"] == am["parents"]["gate2_freeze_sha256"] and V.freeze_sha(g2) == g2["freeze_sha256"]
    blocks = am["design"]["blocks"]; sched = A.amended_schedule(blocks)
    assert V.schedule_sha(sched) == am["amended_schedule_sha256"] and len(sched) == am["design"]["physical_launches"] == 146
    assert sum(1 for s in sched if s["kind"] == "counted") == am["design"]["counted_launches"] == 144 and sum(1 for s in sched if s["kind"] == "warmup") == 2
    assert blocks == {"1.5": 18, "8": 6} and "4" not in blocks
    frozen = {s["seq"]: s for s in V.timing_schedule()}
    assert all({k: v for k, v in s.items() if k not in ("seq", "frozen_seq")} == {k: v for k, v in frozen[s["frozen_seq"]].items() if k != "seq"} for s in sched), "amended slots are frozen slots"
    assert [s["seq"] for s in sched] == list(range(1, 147))
    assert V.BUDGETS["1.5"]["n_blocks"] == 18 and V.PRIMARY_BUDGET == "1.5" and V.ARMS == am["unchanged"]["arms"] and V.RUNTIME_SHA256 == am["unchanged"]["runtime_sha256"] and V.TOKEN_DIGEST == am["unchanged"]["token_digest"]
    tm = json.loads((HERE / "UPDATED_TIME_MODEL.json").read_text())
    for name, b in {"FULL": {"1.5": 18, "4": 6, "8": 6}, "PRIMARY_PLUS_8G": {"1.5": 18, "8": 6}, "PRIMARY_ONLY": {"1.5": 18}}.items():
        for sh in ("helper", "legacy"):
            for sc in T.SCENARIOS:
                assert json.loads(json.dumps(T.hours(b, sc, sh))) == tm["designs"][name][f"{sh}_{sc}"], (name, sh, sc)
    assert tm["designs"]["FULL"]["helper_central"]["physical_launches"] == 183
    return {"ok": True, "freeze_sha256": fz["freeze_sha256"]}


if __name__ == "__main__":
    print(validate())
