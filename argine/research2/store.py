"""Mutable local research-state storage, kept OUTSIDE Git.

Tracked roadmap/protocol/baseline config under research2/ is immutable
research planning; this module owns everything that changes as experiments
run: per-experiment state, the run registry, and run directories.

Default evidence root: %LOCALAPPDATA%\\Argine3\\research (Windows).
Override: ARGINE3_RESEARCH_ROOT (used by tests and anyone developing off
Windows). No user-specific absolute path is hard-coded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

STATE_FILENAME = "state.json"
REGISTRY_FILENAME = "registry.json"


def evidence_root() -> Path:
    override = os.environ.get("ARGINE3_RESEARCH_ROOT")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "Argine3" / "research"
    # Non-Windows fallback (development/tests only; doctor/audit report the
    # platform as unsupported for production use -- see cli.py doctor).
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "argine3" / "research"


def ensure_root_writeable(root: Optional[Path] = None) -> None:
    """Cheap check: create the root (and a marker file) if needed. Raises
    OSError if the location is not writeable. Does no heavy I/O."""
    root = root or evidence_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".write-check"
    marker.write_text("ok", encoding="utf-8")
    marker.unlink()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_state(root: Optional[Path] = None) -> Dict[str, dict]:
    """{experiment_id: {"state": "...", ...}} for every experiment that has
    ever had its state touched. Experiments absent here use the roadmap's
    declared initial_state."""
    root = root or evidence_root()
    return _read_json(root / STATE_FILENAME, {})


def save_state(experiment_states: Dict[str, dict], root: Optional[Path] = None) -> None:
    root = root or evidence_root()
    _write_json(root / STATE_FILENAME, experiment_states)


def set_experiment_state(experiment_id: str, new_state: str, root: Optional[Path] = None) -> None:
    root = root or evidence_root()
    states = load_state(root)
    states[experiment_id] = {**states.get(experiment_id, {}), "state": new_state}
    save_state(states, root)


def load_registry(root: Optional[Path] = None) -> List[dict]:
    root = root or evidence_root()
    return _read_json(root / REGISTRY_FILENAME, [])


def append_registry_entry(entry: dict, root: Optional[Path] = None) -> None:
    root = root or evidence_root()
    reg = load_registry(root)
    reg.append(entry)
    _write_json(root / REGISTRY_FILENAME, reg)


def run_dir(experiment_id: str, run_id: str, root: Optional[Path] = None) -> Path:
    root = root or evidence_root()
    return root / experiment_id / run_id
