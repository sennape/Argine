"""Core logic behind `argine research2 {status,doctor,next,run,analyse,audit}`.

Kept deliberately free of heavy imports (no model loading, no runtime
startup) so that status/doctor/audit stay cheap. run()/analyse() are the
generic, pluggable contract for R2-01..R2-12: EXPERIMENT_IMPLEMENTATIONS is
empty in R2-M0.1 by design (ZERO scientific findings yet) and is the single
place a future experiment plugs in without replacing this framework.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import provenance, roadmap as roadmap_mod, state as state_mod, store

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNKNOWN_ID = 12
EXIT_DIRTY_REFUSED = 13
EXIT_NOT_IMPLEMENTED = 14
EXIT_AUDIT_FAILED = 15
EXIT_CONFIG_INVALID = 16
EXIT_BLOCKED = 17            # e.g. TRACE_SOURCE_MISSING / TRACE_SOURCE_AMBIGUOUS
EXIT_SCIENTIFIC_FAIL = 18    # the run completed but outcome == FAIL

# Pluggable experiment implementations. Empty in R2-M0.1. A future change
# adds entries here (or via a registration hook) rather than editing run()/
# analyse(); see docs/research2/AUTOMATION.md.
EXPERIMENT_RUN_IMPLEMENTATIONS: Dict[str, Callable[..., Dict[str, Any]]] = {}
EXPERIMENT_ANALYSE_IMPLEMENTATIONS: Dict[str, Callable[..., Dict[str, Any]]] = {}
EXPERIMENT_CAPTURE_IMPLEMENTATIONS: Dict[str, Callable[..., Dict[str, Any]]] = {}
# exp_id -> callable(local_states) -> cheap extra status fields (status_report()
# must stay cheap: no hashing, no model/runtime startup -- see R2-01A section 10).
EXPERIMENT_STATUS_EXTRA: Dict[str, Callable[[Dict[str, dict]], Dict[str, Any]]] = {}


def _register_implementations() -> None:
    """Deferred import: R2-01's implementation registers itself here rather
    than at module import time, so importing `runner` alone never pulls in
    argine.research/numpy-ish heavier modules unless an experiment is
    actually implemented (section 15: the framework stays pluggable)."""
    if "R2-01" in EXPERIMENT_RUN_IMPLEMENTATIONS:
        return
    from .r2_01 import orchestrator as _r2_01

    def _run_r2_01(allow_dirty: bool = False, trace_source_arg: Optional[str] = None,
                  **_ignored: Any) -> Dict[str, Any]:
        return _r2_01.run(trace_source_arg=trace_source_arg, allow_dirty=allow_dirty)

    def _analyse_r2_01(run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
        return _r2_01.analyse(run_id=run_id)

    def _capture_r2_01(allow_dirty: bool = False, model_path: Optional[str] = None,
                       dry_run: bool = True, **_ignored: Any) -> Dict[str, Any]:
        from .r2_01 import capture as _capture
        if not model_path:
            return {"experiment_id": "R2-01", "outcome": "USAGE_ERROR",
                    "message": "capture requires --model PATH (a local Qwen3-30B-A3B "
                               "GGUF file)."}
        return _capture.capture(model_path=model_path, allow_dirty=allow_dirty,
                                dry_run=dry_run)

    def _status_extra_r2_01(local_states: Dict[str, dict]) -> Dict[str, Any]:
        from .r2_01 import trace_source as _trace_source
        local = local_states.get("R2-01", {})
        registration = local.get("trace_source_registration")
        local_state_path = (registration or {}).get("path") or local.get("trace_source")
        # Cheap: resolve() with no explicit arg only stats local candidate
        # paths (discover_local_candidates), never hashes a multi-GB trace.
        resolution = _trace_source.resolve(local_state_source=local_state_path)
        runnable = resolution.status == _trace_source.TRACE_SOURCE_RESOLVED
        return {
            "trace_source_status": resolution.status,
            "trace_source_type": (registration or {}).get("source_type"),
            "trace_source_runnable": runnable,
            "trace_source_blocker": None if runnable else resolution.message,
        }

    EXPERIMENT_RUN_IMPLEMENTATIONS["R2-01"] = _run_r2_01
    EXPERIMENT_ANALYSE_IMPLEMENTATIONS["R2-01"] = _analyse_r2_01
    EXPERIMENT_CAPTURE_IMPLEMENTATIONS["R2-01"] = _capture_r2_01
    EXPERIMENT_STATUS_EXTRA["R2-01"] = _status_extra_r2_01


_register_implementations()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(_repo_root()), *args],
                          capture_output=True, text=True, timeout=15)


def git_is_clean() -> Optional[bool]:
    try:
        r = _git("status", "--porcelain", "--untracked-files=no")
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() == ""


def _load_baseline() -> dict:
    import json
    p = _repo_root() / "research2" / "baseline.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def effective_state(experiment_id: str, rm: roadmap_mod.Roadmap,
                    local_states: Dict[str, dict]) -> str:
    override = local_states.get(experiment_id, {}).get("state")
    exp = rm.experiments.get(experiment_id, {})
    base = override or exp.get("initial_state", "PLANNED")
    if base == "PLANNED" and rm.is_implemented(experiment_id):
        # PLANNED -> IMPLEMENTED is a legal protocol.json transition; this is
        # the implied state once an implementation has landed, computed
        # read-only here rather than written through set_experiment_state
        # (status/doctor/next must never mutate state).
        return "IMPLEMENTED"
    return base


def next_eligible(rm: roadmap_mod.Roadmap, local_states: Dict[str, dict]) -> Optional[dict]:
    """First experiment (canonical roadmap order) that is not COMPLETED and
    whose dependencies are all COMPLETED. Returns a dict describing it, or
    None if every experiment is COMPLETED."""
    for exp_id in rm.ordered_experiment_ids():
        cur_state = effective_state(exp_id, rm, local_states)
        if cur_state == "COMPLETED":
            continue
        deps = rm.depends_on(exp_id)
        deps_done = all(effective_state(d, rm, local_states) == "COMPLETED" for d in deps)
        if not deps_done:
            continue
        implemented = rm.is_implemented(exp_id)
        return {
            "experiment_id": exp_id,
            "name": rm.experiments[exp_id]["name"],
            "state": cur_state,
            "implemented": implemented,
            "runnable": implemented,
        }
    return None


# --------------------------------------------------------------- status ---

def status_report() -> Dict[str, Any]:
    """Cheap and READ-ONLY. Never hashes the model, never starts the
    runtime, never modifies repository or scientific state."""
    rm = roadmap_mod.load()
    local_states = store.load_state()
    baseline = _load_baseline()
    head = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    clean = git_is_clean()

    experiments = {}
    for exp_id in rm.ordered_experiment_ids():
        experiments[exp_id] = {
            "name": rm.experiments[exp_id]["name"],
            "phase": rm.experiments[exp_id]["phase"],
            "state": effective_state(exp_id, rm, local_states),
            "implemented": rm.is_implemented(exp_id),
        }
        extra = EXPERIMENT_STATUS_EXTRA.get(exp_id)
        if extra is not None:
            # Each registered extra is documented cheap (no hashing, no
            # model/runtime startup) by the implementation that registers
            # it -- status_report() itself performs no heavy work here.
            experiments[exp_id].update(extra(local_states))

    nxt = next_eligible(rm, local_states)
    return {
        "baseline_sha": baseline.get("baseline_sha"),
        "current_sha": head.stdout.strip() if head.returncode == 0 else None,
        "current_branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "working_tree": "clean" if clean else ("dirty" if clean is False else "unknown"),
        "current_phase": _current_phase(rm, local_states),
        "experiments": experiments,
        "next_eligible": nxt,
        "evidence_root": str(store.evidence_root()),
    }


def _current_phase(rm: roadmap_mod.Roadmap, local_states: Dict[str, dict]) -> Optional[str]:
    for phase in rm.phases:
        exps = phase.get("experiments", [])
        if any(effective_state(e, rm, local_states) != "COMPLETED" for e in exps):
            return phase["id"]
    return None


# --------------------------------------------------------------- doctor ---

def doctor_report() -> Dict[str, Any]:
    """Read-only environment/readiness diagnostic. No model start, no heavy
    artifact hashing (that belongs to an actual run)."""
    checks: Dict[str, Any] = {}
    checks["supported_os"] = {"value": sys.platform, "ok": sys.platform.startswith("win"),
                              "note": None if sys.platform.startswith("win") else
                              "Argine3/research2 targets Windows; other platforms are for "
                              "development/testing only"}
    checks["python"] = {"value": sys.version.split()[0], "ok": sys.version_info >= (3, 9)}
    checks["repository_root"] = {"value": str(_repo_root()), "ok": (_repo_root() / ".git").exists()}
    checks["git_available"] = {"value": shutil.which("git"), "ok": shutil.which("git") is not None}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    checks["current_branch"] = {"value": branch.stdout.strip() if branch.returncode == 0 else None,
                                "ok": branch.returncode == 0}
    clean = git_is_clean()
    checks["git_clean"] = {"value": clean, "ok": True, "note": "informational only; doctor does "
                           "not gate on this"}
    import os as _os
    llama_dir = _os.environ.get("ARGINE_LLAMA_DIR")
    checks["runtime_binary_hint"] = {
        "value": llama_dir, "ok": True,
        "note": "informational only: this is simply the current ARGINE_LLAMA_DIR "
               "environment variable (see argine/runtime.py:llama_dir_configurato), not "
               "something research2 derives or resolves itself. On this project's "
               "machine it has been observed pointing at the original Argine v1 "
               "repository's build (...\\Argine\\llama-noomp), not Argine2's or "
               "Argine3's own research runtime (llama-research / llama-research-v2) -- "
               "i.e. likely stale leftover from before Argine2 existed. R2-01's offline "
               "trace/replay contract validation does not use or require this value at "
               "all; a future real trace CAPTURE would need its own explicit runtime "
               "artifact resolution rather than trusting this hint.",
    }
    try:
        store.ensure_root_writeable()
        checks["evidence_root_writeable"] = {"value": str(store.evidence_root()), "ok": True}
    except OSError as e:
        checks["evidence_root_writeable"] = {"value": str(store.evidence_root()), "ok": False,
                                             "note": str(e)}
    try:
        rm = roadmap_mod.load()
        rm_problems = rm.validate()
        checks["roadmap_valid"] = {"value": rm.path.name, "ok": not rm_problems,
                                   "problems": rm_problems}
    except roadmap_mod.RoadmapError as e:
        checks["roadmap_valid"] = {"value": None, "ok": False, "problems": [str(e)]}
    try:
        proto = state_mod.load()
        proto_problems = proto.validate()
        checks["protocol_valid"] = {"value": "protocol.json", "ok": not proto_problems,
                                    "problems": proto_problems}
    except state_mod.ProtocolError as e:
        checks["protocol_valid"] = {"value": None, "ok": False, "problems": [str(e)]}

    overall_ok = all(c.get("ok", False) for c in checks.values()
                     if c is not checks.get("git_clean"))
    return {"ok": overall_ok, "checks": checks}


# ----------------------------------------------------------------- audit --

def audit_report() -> Dict[str, Any]:
    problems: Dict[str, list] = {}
    try:
        rm = roadmap_mod.load()
        problems["roadmap"] = rm.validate()
    except roadmap_mod.RoadmapError as e:
        problems["roadmap"] = [str(e)]
        rm = None
    try:
        proto = state_mod.load()
        problems["protocol"] = proto.validate()
    except state_mod.ProtocolError as e:
        problems["protocol"] = [str(e)]
        proto = None

    baseline_problems = []
    baseline = _load_baseline()
    sha = baseline.get("baseline_sha")
    if not sha or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        baseline_problems.append(f"baseline_sha missing or not a 40-hex-char SHA: {sha!r}")
    else:
        anc = _git("merge-base", "--is-ancestor", sha, "HEAD")
        if anc.returncode != 0:
            baseline_problems.append(
                f"baseline_sha {sha} is not an ancestor of current HEAD")
    problems["baseline_identity"] = baseline_problems

    dir_problems = []
    for rel in ("research2", "docs/research2"):
        if not (_repo_root() / rel).is_dir():
            dir_problems.append(f"expected directory missing: {rel}")
    if rm:
        for exp_id, exp in rm.experiments.items():
            # experiment dirs are named R2-NN-slug; just check a R2-NN* prefix exists
            matches = list((_repo_root() / "experiments").glob(f"{exp_id}-*"))
            if not matches:
                dir_problems.append(f"no experiments/{exp_id}-* directory found")
    problems["expected_directories"] = dir_problems

    evidence_problems = []
    try:
        root = store.evidence_root()
        if not isinstance(root, Path) or not str(root):
            evidence_problems.append("evidence_root() did not return a usable path")
    except Exception as e:  # noqa: BLE001
        evidence_problems.append(f"evidence_root() raised: {e}")
    problems["evidence_root_configuration"] = evidence_problems

    provenance_problems = []
    sample = provenance.build_record("R2-AUDIT-PROBE", "NOT_IMPLEMENTED")
    provenance_problems.extend(provenance.validate_record_shape(sample))
    problems["provenance_schema"] = provenance_problems

    registry_problems = []
    if rm:
        for entry in store.load_registry():
            exp_id = entry.get("identity", {}).get("experiment_id") if "identity" in entry \
                else entry.get("experiment_id")
            if exp_id and exp_id not in rm.experiments and not exp_id.startswith("R2-AUDIT-PROBE"):
                registry_problems.append(f"registry entry references unknown experiment id: {exp_id}")
    problems["experiment_registry_integrity"] = registry_problems

    state_problems = []
    if proto:
        local_states = store.load_state()
        for exp_id, rec in local_states.items():
            s = rec.get("state")
            if s and not proto.is_valid_state(s):
                state_problems.append(f"{exp_id}: stored state {s!r} is not a declared protocol state")
    problems["stored_state_validity"] = state_problems

    ok = all(not v for v in problems.values())
    return {"ok": ok, "problems": problems}


# ------------------------------------------------------------- run/analyse

def _validated_experiment(rm: roadmap_mod.Roadmap, experiment_id: str) -> None:
    if experiment_id not in rm.experiments:
        raise KeyError(f"unknown experiment id: {experiment_id!r} "
                       f"(known: {', '.join(sorted(rm.experiments))})")


def run_experiment(experiment_id: str, *, allow_dirty: bool = False, **kwargs: Any
                   ) -> Dict[str, Any]:
    """Generic experiment-run contract. For every experiment currently in
    EXPERIMENT_RUN_IMPLEMENTATIONS: dispatches to it (passing allow_dirty
    plus any experiment-specific kwargs, e.g. trace_source_arg for R2-01).
    Otherwise: validate ID, fail closed on a dirty tree (unless allowed),
    then return NOT_IMPLEMENTED -- and create no run directory, no
    provenance file, no false evidence."""
    rm = roadmap_mod.load()
    _validated_experiment(rm, experiment_id)  # raises KeyError if unknown

    clean = git_is_clean()
    # dirty is True unless the tree was POSITIVELY verified clean; an
    # unknown git state (clean is None -- e.g. the git subprocess was
    # unavailable or timed out, which has been observed to happen
    # transiently against this repository) must fail closed exactly like a
    # confirmed-dirty tree, never silently be treated as clean.
    dirty = (clean is not True)
    unverified = (clean is None)
    if dirty and not allow_dirty:
        return {
            "experiment_id": experiment_id,
            "outcome": "REFUSED_DIRTY_TREE",
            "candidate_status": provenance.candidate_status(dirty, False),
            "message": (
                "working tree cleanliness could not be verified (the git status "
                "check failed or timed out); candidate scientific runs refuse this "
                "by default. Pass --allow-dirty to proceed (the run is then "
                "permanently marked NON-CANDIDATE)."
                if unverified else
                "working tree is dirty; candidate scientific runs refuse this by "
                "default. Pass --allow-dirty to proceed (the run is then permanently "
                "marked NON-CANDIDATE)."
            ),
        }

    impl = EXPERIMENT_RUN_IMPLEMENTATIONS.get(experiment_id)
    if impl is None:
        return {
            "experiment_id": experiment_id,
            "outcome": "NOT_IMPLEMENTED",
            "candidate_status": provenance.candidate_status(dirty, allow_dirty),
            "message": f"{experiment_id} has no implementation yet (R2-M0.1 is "
                       f"infrastructure-only). No run directory or provenance was created.",
        }
    return impl(allow_dirty=allow_dirty, **kwargs)


def capture_experiment(experiment_id: str, *, allow_dirty: bool = False, **kwargs: Any
                       ) -> Dict[str, Any]:
    """R2-01A: generic fresh-capture-readiness contract, gated exactly like
    run_experiment() (same fail-closed dirty/unverified-tree semantics).
    Experiments without a registered capture implementation return
    NOT_IMPLEMENTED, same as run_experiment()."""
    _register_implementations()
    rm = roadmap_mod.load()
    _validated_experiment(rm, experiment_id)

    clean = git_is_clean()
    dirty = (clean is not True)
    unverified = (clean is None)
    if dirty and not allow_dirty:
        return {
            "experiment_id": experiment_id,
            "outcome": "REFUSED_DIRTY_TREE",
            "candidate_status": provenance.candidate_status(dirty, False),
            "message": (
                "working tree cleanliness could not be verified (the git status "
                "check failed or timed out); capture refuses this by default. "
                "Pass --allow-dirty to proceed (the capture is then permanently "
                "marked NON-CANDIDATE)."
                if unverified else
                "working tree is dirty; capture refuses this by default. Pass "
                "--allow-dirty to proceed (the capture is then permanently marked "
                "NON-CANDIDATE)."
            ),
        }

    impl = EXPERIMENT_CAPTURE_IMPLEMENTATIONS.get(experiment_id)
    if impl is None:
        return {
            "experiment_id": experiment_id,
            "outcome": "NOT_IMPLEMENTED",
            "candidate_status": provenance.candidate_status(dirty, allow_dirty),
            "message": f"{experiment_id} has no capture implementation.",
        }
    return impl(allow_dirty=allow_dirty, **kwargs)


def analyse_experiment(experiment_id: str, **kwargs: Any) -> Dict[str, Any]:
    rm = roadmap_mod.load()
    _validated_experiment(rm, experiment_id)
    impl = EXPERIMENT_ANALYSE_IMPLEMENTATIONS.get(experiment_id)
    if impl is None:
        return {
            "experiment_id": experiment_id,
            "outcome": "NOT_IMPLEMENTED",
            "message": f"{experiment_id} has no raw evidence / analysis implementation yet.",
        }
    return impl(**kwargs)
