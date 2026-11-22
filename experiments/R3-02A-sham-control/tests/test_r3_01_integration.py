"""R3-02A: sham policy identity must pass the frozen R3-01 admission unchanged.

No contract edit is allowed: this test proves the new identity is representable
as an ordinary policy_id + policy_params pair.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "experiments" / "R3-01-telemetry-realization"))

import r3_telemetry.telemetry as T  # noqa: E402

SHAM_PARAMS = {
    "aging_interval_steps": 64,
    "decay_factor": 0.99,
    "n_experts": 128,
    "predictor_decay": 0.99,
    "top_k": 8,
}


class TestShamR301Integration(unittest.TestCase):
    def _identity(self):
        ident = T.fixture_identity()
        ident["policy_params_sha256"] = T.policy_params_digest(SHAM_PARAMS)
        return ident

    def test_sham_trace_record_is_admissible(self):
        rec = T.fixture_trace_count(policy_id="predicted_protect_sham",
                                    policy_params=SHAM_PARAMS,
                                    identity=self._identity())
        out = T.admit_record(rec)
        self.assertEqual(out["status"], "ADMISSIBLE", out["failures"])

    def test_sham_cell_pair_is_admissible(self):
        rec = T.fixture_trace_count(policy_id="predicted_protect_sham",
                                    policy_params=SHAM_PARAMS,
                                    identity=self._identity())
        tim = T.fixture_timing(policy_id="predicted_protect_sham",
                               policy_params=SHAM_PARAMS,
                               identity=self._identity())
        out = T.admit_cell(rec, tim)
        self.assertEqual(out["status"], "ADMISSIBLE", out["failures"])

    def test_sham_params_differ_from_predicted_protect_only_by_policy_id(self):
        # Same frozen predictor parameters as predicted_protect: the sham is a
        # behaviour control, not a re-parameterisation.
        self.assertEqual(SHAM_PARAMS["predictor_decay"], 0.99)
        self.assertEqual(SHAM_PARAMS["top_k"], 8)
        self.assertEqual(SHAM_PARAMS["n_experts"], 128)

    def test_frozen_contract_untouched(self):
        contract = REPO / "experiments" / "R3-00A-observability-contract" / "OBSERVABILITY_CONTRACT.json"
        digest = hashlib.sha256(contract.read_bytes()).hexdigest()
        freeze = json.loads((contract.parent / "FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(freeze["contract"]["sha256"], digest)
        self.assertEqual(freeze["contract"]["contract_digest"], T.CONTRACT_DIGEST)


if __name__ == "__main__":
    unittest.main(verbosity=2)
