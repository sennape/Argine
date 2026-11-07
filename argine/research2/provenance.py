"""The research2 provenance schema (R2-M0.1 contract only; not populated by a
real run yet -- no experiment is implemented).

Reuses argine.research.fingerprint for git state and machine identity
rather than reimplementing it, per the existing Research Part 1 convention.
Heavy artifact hashing (the real model, a multi-GiB runtime binary) is
deliberately NOT performed here: this module only *builds the shape* of the
record; callers pass in already-available cheap identity info, and an actual
future experiment run is responsible for the expensive artifact hashes.
"""

from __future__ import annotations

import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


def new_run_id() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def git_identity() -> Dict[str, Any]:
    """Thin wrapper around argine.research.fingerprint.git_state(), reshaped
    into the research2 provenance Git field group."""
    try:
        from ..research.fingerprint import git_state
    except Exception as e:  # noqa: BLE001
        return {"repo_root": str(_repo_root()), "branch": None, "commit": None,
                "clean": None, "note": f"fingerprint unavailable: {e}"}
    gs = git_state(_repo_root())
    branch = None
    try:
        import subprocess
        b = subprocess.run(["git", "-C", str(_repo_root()), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        if b.returncode == 0:
            branch = b.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return {
        "repo_root": str(_repo_root()),
        "branch": branch,
        "commit": gs.get("commit"),
        "clean": (not gs["dirty"]) if "dirty" in gs else None,
        "dirty_definition": gs.get("dirty_definition"),
        "note": gs.get("note"),
    }


def software_identity() -> Dict[str, Any]:
    argine_version = None
    try:
        from .. import __version__ as argine_version  # type: ignore
    except Exception:  # noqa: BLE001
        pass
    return {
        "python_version": sys.version.split()[0],
        "argine_version": argine_version,
        "os": platform.platform(),
        "runtime_identity": None,  # filled by an actual future run (llama.cpp build identity)
    }


def hardware_identity() -> Dict[str, Any]:
    try:
        from ..research.fingerprint import machine_identity
        return machine_identity()
    except Exception as e:  # noqa: BLE001
        return {"cpu": None, "note": f"fingerprint unavailable: {e}"}


def candidate_status(dirty: Optional[bool], allow_dirty: bool) -> str:
    """CANDIDATE only when the tree was positively verified clean
    (dirty is False). Any other state -- confirmed dirty (True) OR
    unknown/unverifiable (None, e.g. the git subprocess was unavailable or
    timed out, which has been observed to happen transiently against this
    repository) -- must never silently become candidate scientific
    evidence. A dirty-or-unknown+allowed run is permanently marked
    NON-CANDIDATE; a dirty-or-unknown+disallowed run is REFUSED."""
    if dirty is False:
        return "CANDIDATE"
    return "NON-CANDIDATE" if allow_dirty else "REFUSED"


def build_record(
    experiment_id: str,
    outcome: str,
    *,
    run_id: Optional[str] = None,
    start_utc: Optional[str] = None,
    end_utc: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    seed: Optional[int] = None,
    workload: Optional[str] = None,
    baseline_policy: Optional[str] = None,
    command: Optional[List[str]] = None,
    exit_code: Optional[int] = None,
    stdout_path: Optional[str] = None,
    stderr_path: Optional[str] = None,
    raw_evidence_paths: Optional[List[str]] = None,
    model_artifact: Optional[Dict[str, Any]] = None,
    runtime_binary_artifact: Optional[Dict[str, Any]] = None,
    expert_store_artifact: Optional[Dict[str, Any]] = None,
    allow_dirty: bool = False,
) -> Dict[str, Any]:
    """Build one provenance record covering every required field group:
    identity, git, software, hardware, artifacts, experiment, execution."""
    git = git_identity()
    dirty = (not git["clean"]) if git.get("clean") is not None else None
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "experiment_id": experiment_id,
            "run_id": run_id or new_run_id(),
            "start_utc": start_utc,
            "end_utc": end_utc,
            "outcome": outcome,
            "runner_schema_version": SCHEMA_VERSION,
        },
        "git": {**git, "candidate_status": candidate_status(dirty, allow_dirty)},
        "software": software_identity(),
        "hardware": hardware_identity(),
        "artifacts": {
            "model": model_artifact,
            "runtime_binary": runtime_binary_artifact,
            "expert_store": expert_store_artifact,
        },
        "experiment": {
            "parameters": parameters or {},
            "seed": seed,
            "workload": workload,
            "baseline_policy": baseline_policy,
        },
        "execution": {
            "command": command,
            "exit_code": exit_code,
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "raw_evidence_paths": raw_evidence_paths or [],
        },
    }


REQUIRED_FIELD_GROUPS = ("identity", "git", "software", "hardware", "artifacts",
                         "experiment", "execution")


def validate_record_shape(record: Dict[str, Any]) -> List[str]:
    problems = []
    for group in REQUIRED_FIELD_GROUPS:
        if group not in record:
            problems.append(f"provenance record missing required field group: {group}")
    return problems
