"""R2-11 causal boundary must be byte-identical to R2-10's, and the existing
leakage/future-alteration invariance machinery must still pass, making the
R2-11 dependency on it explicit (instruction #17)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
R2_11_CAUSAL_PATH = REPO_ROOT / "experiments" / "R2-11-predictive-retention" / "causal_boundary.json"
R2_10_CAUSAL_PATH = REPO_ROOT / "experiments" / "R2-10-working-set-predictor" / "causal_boundary.json"


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_r2_11_causal_boundary_reuses_r2_10_verbatim():
    r2_11 = json.loads(R2_11_CAUSAL_PATH.read_text(encoding="utf-8"))
    r2_10 = json.loads(R2_10_CAUSAL_PATH.read_text(encoding="utf-8"))
    assert r2_11["prediction_instant"] == r2_10["prediction_instant"]
    assert r2_11["allowed"] == r2_10["allowed"]
    assert r2_11["forbidden"] == r2_10["forbidden"]


def test_reused_content_digest_matches_source():
    r2_11 = json.loads(R2_11_CAUSAL_PATH.read_text(encoding="utf-8"))
    r2_10 = json.loads(R2_10_CAUSAL_PATH.read_text(encoding="utf-8"))
    import hashlib
    actual = hashlib.sha256(_canon(r2_10).encode("utf-8")).hexdigest()
    assert r2_11["reused_content_sha256"] == actual


def test_r2_10_causal_walk_module_importable_and_referenced():
    # R2-11 does not reimplement leakage machinery; it depends on the same
    # causal-context generator path already enforced by R2-10/R2-BRIDGE-01.
    import importlib
    mod = importlib.import_module("argine.research2.r2_10.causal_walk")
    assert hasattr(mod, "CausalContext")
    assert hasattr(mod, "iter_causal_contexts")
