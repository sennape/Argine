"""Phase 2 readiness: the protocol quotes the frozen contract verbatim, the freeze covers every source, no outcome or
opening artifact exists, and the evaluation stays locked."""
from __future__ import annotations

import json, sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import HERE, load   # noqa: E402

P2 = load("phase2", HERE / "phase2.py")
P0 = HERE.parents[0] / "R5-00-exposed-transfer-stall"


class Readiness(unittest.TestCase):
    def test_verify_ok(self):
        self.assertEqual(P2.verify(), [])

    def test_frozen_texts_verbatim(self):
        proto = json.loads((HERE / "PROTOCOL_2.json").read_text(encoding="utf-8"))
        q = proto["frozen_texts_quoted"]
        self.assertEqual(q["schedule"], json.loads((P0 / "SCHEDULE.json").read_text(encoding="utf-8")))
        dr = json.loads((P0 / "DECISION_RULES.json").read_text(encoding="utf-8"))
        self.assertEqual(q["classification_order"], dr["classification_order"])
        self.assertEqual(q["gates"]["G3_primary"], dr["gates"]["G3_primary"])
        self.assertEqual(q["arms"], json.loads((P0 / "COMPARATORS.json").read_text(encoding="utf-8"))["arms"])
        self.assertEqual(proto["counts_confirmed_against_the_contract"]["per_campaign"],
                         {"mechanism_1.5GiB": 36, "timing_1.5GiB": 80, "timing_4GiB": 80})
        self.assertEqual(proto["counts_confirmed_against_the_contract"]["counted_total"], 196)

    def test_changed_frozen_text_detected(self):
        real = P2._j
        def bad(name):
            d = real(name)
            if name == "DECISION_RULES.json":
                d["thresholds"]["minimum_effect_of_interest"] = 0.01
            return d
        with mock.patch.object(P2, "_j", side_effect=bad):
            self.assertTrue(any("PROTOCOL_2" in x for x in P2.verify()))

    def test_changed_source_detected(self):
        real = P2.lf_sha
        target = (HERE / "analysis.py").resolve()
        with mock.patch.object(P2, "lf_sha", side_effect=lambda p: "0" * 64 if Path(p).resolve() == target else real(p)):
            self.assertTrue(any("READINESS_2" in x for x in P2.verify()))

    def test_opening_or_outcome_artifact_detected(self):
        for name in ("EVALUATION_OPENING.json", "PHASE2_RESULT.json", "PHASE2_LOG.jsonl"):
            f = HERE / name
            try:
                f.write_text("{}", encoding="utf-8")
                self.assertTrue(any("outcome or opening" in x for x in P2.verify()), name)
            finally:
                f.unlink()

    def test_readiness_binds_upstream_seals(self):
        r = json.loads((HERE / "READINESS_2.json").read_text(encoding="utf-8"))
        self.assertEqual((r["g1_freeze_sha256"], r["g2_freeze_sha256"], r["phase1b_readiness_sha256"]), (P2.G1_FREEZE, P2.G2_FREEZE, P2.P1B_READINESS))
        self.assertEqual(r["phase0"], {"contract_digest": P2.P0_CONTRACT_DIGEST, "freeze_sha256": P2.P0_FREEZE})
        self.assertFalse(r["evaluation_opened"]); self.assertEqual(r["eval_state"], "LOCKED")
        self.assertTrue(set(P2.SOURCES) <= set(r["sources_lf_sha256"]))

    def test_no_personal_path_and_no_workload_copy(self):
        users = ":" + "\\" + "Users" + "\\"
        for q in HERE.rglob("*"):
            if q.is_file() and "__pycache__" not in q.parts:
                t = q.read_text(encoding="utf-8", errors="replace")
                self.assertFalse(users in t or users.replace("\\", "\\\\") in t, q)
        self.assertFalse(any(q.name.endswith(("coding.json", "natural-language.json")) for q in HERE.rglob("*")))


if __name__ == "__main__":
    unittest.main()
