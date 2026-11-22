"""R2-10 Phase 0: causal working-set predictor benchmark contract.

This package freezes and validates the R2-10 Phase-0 contract (causal
boundary, output contract, DEV/EVAL binding, predictor candidate grid,
selection rule, acceptance categories) and provides the reference
causal-event walker and output-contract enforcement that any R2-10
predictor must use. It implements ONLY the two trivial/null
causal_runtime_candidate baselines (global_frequency, last_observation)
needed to make the leakage/determinism/cardinality tests executable in
Phase 0. It does NOT run the full DEV predictor tournament, does NOT
implement R2-11 retention, does NOT run R2-12, and does NOT open or read
EVAL data.
"""

from .contract import (
    CONTRACT_VERSION,
    compute_contract_digest,
    default_contract_dir,
    load_contract,
    validate_contract,
)

__all__ = [
    "CONTRACT_VERSION",
    "compute_contract_digest",
    "default_contract_dir",
    "load_contract",
    "validate_contract",
]
