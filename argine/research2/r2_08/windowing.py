"""R2-08 temporal windows (section 15): exactly 8 contiguous chronological windows."""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

N_WINDOWS = 8


def window_definition(h: int) -> Dict[str, object]:
    w = h // N_WINDOWS
    used = N_WINDOWS * w
    trimmed = h - used
    return {
        "n_windows": N_WINDOWS,
        "h": h,
        "window_length": w,
        "positions_used": used,
        "positions_trimmed": trimmed,
        "windows": [{"id": f"window_{i}", "start": i * w, "end_exclusive": (i + 1) * w}
                    for i in range(N_WINDOWS)],
    }


def split_into_windows(positions: Sequence, h: int) -> Tuple[List[List], Dict[str, object]]:
    """Split the first h elements into 8 contiguous windows (trim remainder)."""
    defn = window_definition(h)
    w = int(defn["window_length"])
    trimmed = positions[:defn["positions_used"]]
    windows = [list(trimmed[i * w:(i + 1) * w]) for i in range(N_WINDOWS)]
    return windows, defn
