"""R2-09B benchmark/evaluation contract validation (preregistration only).

This package validates and digests the frozen R2-09B contract. It does NOT
run R2-09A experiments, capture EVAL traces, implement R2-10 predictors, run
R2-11 retention experiments, or execute models.
"""

from .contract import (
    CONTRACT_VERSION,
    compute_contract_digest,
    load_contract,
    validate_contract,
)

__all__ = [
    "CONTRACT_VERSION",
    "compute_contract_digest",
    "load_contract",
    "validate_contract",
]
