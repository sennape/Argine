import json, sys
from pathlib import Path
import pytest
H = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(H))
import closure as c

def test_closure_verifies(): assert c.verify() == []
def test_every_freeze_matches_its_commit():
    for s in c.CHAIN: assert c.check_freeze(*s)["commit"] == s[2]
def test_key_numbers_recompute(): assert c.check_numbers(c.key_numbers()) == []
def test_wrong_number_detected(monkeypatch):
    monkeypatch.setattr(c, "STATED", c.STATED + [("R3_04", "P_minus_A_ms_per_token", 5.0, 1e-3)]); assert c.check_numbers(c.key_numbers())
def test_wrong_freeze_sha_detected():
    s = list(c.CHAIN[4]); s[3] = "0" * 64
    with pytest.raises(c.ClosureError): c.check_freeze(*s)
def test_wrong_commit_detected():
    s = list(c.CHAIN[7]); s[2] = c.CHAIN[0][2]      # R4 evidence did not exist at the R3-03A commit
    with pytest.raises(c.ClosureError): c.check_freeze(*s)
def test_claim_boundaries_and_untested():
    assert c.check_docs() == []
    fr = json.loads((H / "FINAL_RESULT.json").read_text()); assert fr["universal_claim_allowed"] is False and fr["causally_recoverable_belady_headroom_known"] is False
    rc = json.loads((H / "RESEARCH_3_CLOSURE.json").read_text())
    assert all(v == "NOT_TESTED" for g in rc["untested_directions"].values() for v in g.values())
    assert rc["research_4_handoff"]["status"] == "NOT OPENED" and set(rc["old_roadmap"]) == set(c.OLD_STAGES)
def test_tamper_detected():
    f = H / "FINAL_RESULT.json"; o = f.read_bytes()
    try:
        f.write_bytes(o.replace(b'"universal_claim_allowed": false', b'"universal_claim_allowed": true')); assert c.verify() != []
    finally: f.write_bytes(o)
