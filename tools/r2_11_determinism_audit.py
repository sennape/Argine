"""R2-11 determinism audit: re-run a repeat plan independently of the
persisted eval_cells/ results and require bit-identical scientific output."""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.r2_11_eval_runner import (
    CAPTURES_BASE, EVAL_DIR, CELLS_DIR, cell_path, run_one_cell, sha256_file,
)
from argine.research.trace import Trace

import os
_ALL_REPEAT_PLAN = [
    # one per category at the primary 4 GiB budget
    ("r2-11-coding-v1", 4.0),
    ("r2-11-instruction-v1", 4.0),
    ("r2-11-reasoning-v1", 4.0),
    ("r2-11-summarization-v1", 4.0),
    # representative secondary-budget repeats
    ("r2-11-coding-v2", 1.5),
    ("r2-11-reasoning-v2", 8.0),
]
_ONLY = os.environ.get("R2_11_DETERMINISM_ONLY")
REPEAT_PLAN = _ALL_REPEAT_PLAN if not _ONLY else _ALL_REPEAT_PLAN[int(_ONLY.split(":")[0]):int(_ONLY.split(":")[1])]

def main():
    em = json.load(open(EVAL_DIR / "eval_manifest.json"))
    bm = json.load(open(EVAL_DIR / "baseline_mapping.json"))
    budget_map = bm["mapping_by_budget_bytes"]
    budget_gib_to_bytes = {v["budget_gib"]: int(k) for k, v in budget_map.items()}

    all_identical = True
    n_checked = 0
    for wl_id, budget_gib in REPEAT_PLAN:
        budget_bytes = budget_gib_to_bytes[budget_gib]
        params = budget_map[str(budget_bytes)]["params"]
        trace_path = CAPTURES_BASE / wl_id / "run-00-4G" / "trace.jsonl.gz"
        trace_sha256 = sha256_file(trace_path)
        trace = Trace.load(trace_path)

        for policy_id in ("predicted_protect", "b_selected"):
            persisted = json.loads(cell_path(wl_id, budget_gib, policy_id).read_text())
            repeat = run_one_cell(wl_id, trace, trace_sha256, budget_bytes, budget_gib,
                                   policy_id, params["decay_factor"], params["aging_interval_steps"])
            # Compare scientific result fields only (elapsed_seconds legitimately
            # varies). per_layer keys are ints in a freshly-computed dict but
            # strings once round-tripped through JSON, so normalize key types
            # before comparing -- this is a serialization-format detail, not a
            # scientific difference.
            def _canon(det):
                det = dict(det)
                det["per_layer"] = {str(k): v for k, v in det["per_layer"].items()}
                return det
            p_det = persisted["result"]
            r_det = repeat["result"]
            identical = (
                json.dumps(_canon(p_det), sort_keys=True) == json.dumps(_canon(r_det), sort_keys=True)
                and persisted["miss_bytes_per_decode_step"] == repeat["miss_bytes_per_decode_step"]
                and persisted["validity_failure"] == repeat["validity_failure"]
            )
            n_checked += 1
            all_identical = all_identical and identical
            print(f"{wl_id} budget={budget_gib}GiB policy={policy_id}: identical={identical}")

    print()
    print(f"n_repeat_cells_checked={n_checked}  all_bit_identical={all_identical}")
    return 0 if all_identical else 1

if __name__ == "__main__":
    raise SystemExit(main())
