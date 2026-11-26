"""Phase 1b readiness: pre-declared protocol, frozen texts quoted verbatim, freeze of the sources, no outcome present."""
from __future__ import annotations

import importlib.util, json, sys, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phase1b", HERE / "phase1b.py")
P = importlib.util.module_from_spec(spec); sys.modules["phase1b"] = P; spec.loader.exec_module(P)  # type: ignore


class Readiness(unittest.TestCase):
    def test_verify_ok(self):
        self.assertEqual(P.verify(), [])

    def test_frozen_rule_texts_quoted_verbatim(self):
        proto = json.loads((HERE / "PROTOCOL_1B.json").read_text(encoding="utf-8"))
        dr = json.loads((P.P0_DIR / "DECISION_RULES.json").read_text(encoding="utf-8"))
        self.assertEqual(proto["frozen_texts_quoted"]["G2_headroom"], dr["gates"]["G2_headroom"])
        self.assertEqual(proto["frozen_texts_quoted"]["headroom_gate"], 0.04)
        self.assertEqual(proto["decision"]["threshold"], 0.04)
        self.assertTrue(proto["decision"]["inclusive"])
        self.assertEqual(proto["schedule"]["counted_launches"], 8)

    def test_changed_frozen_text_or_threshold_is_detected(self):
        real = P.frozen_texts
        with mock.patch.object(P, "frozen_texts", side_effect=lambda: dict(real(), headroom_gate=0.03)):
            self.assertTrue(any("PROTOCOL_1B" in x for x in P.verify()))

    def test_changed_source_is_detected(self):
        real = P.lf_sha
        target = (HERE / "headroom.py").resolve()
        with mock.patch.object(P, "lf_sha", side_effect=lambda p: "0" * 64 if Path(p).resolve() == target else real(p)):
            self.assertTrue(any("READINESS" in x for x in P.verify()))

    def test_outcome_artifact_is_detected(self):
        f = HERE / "G2_RESULT.json"
        try:
            f.write_text("{}", encoding="utf-8")
            self.assertTrue(any("outcome artifacts" in x for x in P.verify()))
        finally:
            f.unlink()

    def test_no_phase2_artifact(self):
        names = [q.name.upper() for q in P.REPO.joinpath("experiments").rglob("*") if q.is_file()]
        self.assertFalse(any("PHASE2" in n and "OPEN" in n for n in names))
        self.assertFalse(any(d.name.startswith("R5-03") for d in P.REPO.joinpath("experiments").iterdir()))


if __name__ == "__main__":
    unittest.main()
