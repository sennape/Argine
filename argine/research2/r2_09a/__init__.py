"""R2-09A causal baseline tournament (DEV-only) under frozen R2-09B v1.1.

Phases: corpus freeze, baseline validation, tournament, B_selected selection/freeze.
Does not inspect/open EVAL results and does not modify R2-01..R2-08 canonical evidence.
"""
from .tournament import run_r2_09a  # noqa: F401
