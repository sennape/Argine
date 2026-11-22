"""R2-04 section 15: LFU remains a SECONDARY causal baseline only. This
module summarizes, per budget, whether LFU beats or loses to Global-LRU
-- never anything more central than that; LFU plays no role in the
recoverable-envelope (structural/information/total) definitions above.
"""

from __future__ import annotations

from typing import Any, Dict, List

LABEL_LFU = "LFU"


def lfu_vs_global_lru(budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                      global_lru_label: str) -> List[Dict[str, Any]]:
    out = []
    for budget in sorted(budget_results):
        lfu_row = budget_results[budget].get(LABEL_LFU)
        glru_row = budget_results[budget].get(global_lru_label)
        if lfu_row is None or glru_row is None:
            continue
        if lfu_row["misses"] < glru_row["misses"]:
            verdict = "LFU_BETTER"
        elif lfu_row["misses"] > glru_row["misses"]:
            verdict = "LFU_WORSE"
        else:
            verdict = "LFU_EQUAL"
        out.append({
            "budget_bytes": budget,
            "verdict": verdict,
            "lfu_misses": lfu_row["misses"],
            "global_lru_misses": glru_row["misses"],
            "lfu_miss_bytes": lfu_row["miss_bytes"],
            "global_lru_miss_bytes": glru_row["miss_bytes"],
        })
    return out
