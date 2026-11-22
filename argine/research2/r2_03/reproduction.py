"""R2-03's mandatory R2-02 reproduction check (section "R2-02 REPRODUCTION
CHECK"): the 4 GiB point of R2-03's own fresh sweep must reproduce R2-02's
already-registered PASS evidence EXACTLY for Global-LRU and Oracle-
Global-Belady. Reads R2-02's own policy_results.json dynamically (never a
hard-coded expected-value literal) -- the "expected" values are always
whatever R2-02's own latest PASS run actually recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

CHECKED_FIELDS = ("misses", "miss_bytes")


@dataclass
class ReproductionCheck:
    ok: bool
    message: str
    mismatches: Dict[str, Dict[str, Any]]

    def to_json(self) -> Dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "mismatches": self.mismatches}


def load_r2_02_rows(r2_02_run_dir: Path) -> Dict[str, Dict[str, Any]]:
    data = json.loads((r2_02_run_dir / "policy_results.json").read_text(encoding="utf-8"))
    return {row["policy"]: row for row in data["policies"]}


def check(r2_02_rows: Dict[str, Dict[str, Any]],
         r2_03_rows_at_4gib: Dict[str, Dict[str, Any]],
         *, policies_to_check: Optional[list] = None) -> ReproductionCheck:
    policies_to_check = policies_to_check or ["Global-LRU", "Oracle-Global-Belady"]
    mismatches: Dict[str, Dict[str, Any]] = {}
    for label in policies_to_check:
        expected = r2_02_rows.get(label)
        actual = r2_03_rows_at_4gib.get(label)
        if expected is None or actual is None:
            mismatches[label] = {"error": f"missing row (expected present={expected is not None}, "
                                          f"actual present={actual is not None})"}
            continue
        for field in CHECKED_FIELDS:
            if expected[field] != actual[field]:
                mismatches.setdefault(label, {})[field] = {
                    "r2_02_value": expected[field], "r2_03_value_at_4gib": actual[field]}
    if mismatches:
        return ReproductionCheck(
            False, f"4 GiB reproduction FAILED for {sorted(mismatches)}: a fresh R2-03 "
                  f"replay at the identical budget/policy/trace must match R2-02's own "
                  f"registered evidence exactly -- any drift is an infrastructure-"
                  f"scientific inconsistency and blocks interpretation of the whole "
                  f"sweep.",
            mismatches)
    return ReproductionCheck(
        True, f"4 GiB point exactly reproduces R2-02's registered PASS evidence for "
             f"{policies_to_check}.", {})
