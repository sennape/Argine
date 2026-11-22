"""R2-01A hotfix (round 2): deterministic llama.cpp runtime resolution for
Research2 capture.

The first real fresh-capture attempt reached the planner and failed only
because the inherited ARGINE_LLAMA_DIR pointed at a stale location (the
original Argine v1 checkout, before the project was reorganised into
Argine2/Argine3) that no longer contains llama-server. That is an
infrastructure gap, not a scientific one: `ensure_runtime()`
(argine/runtime.py) already distinguishes "unset" from "set but invalid"
and already does real capability validation for the expert store
(LlamaRuntime.supports_expert_store()); this module composes those
existing primitives into one capture-specific, narrow, deterministic
resolver -- it never reimplements runtime acquisition, llama-server
invocation, or capability detection.

Precedence (never guesses between multiple equally-valid candidates):
  1. explicit CLI/API runtime override;
  2. a previously-verified Research2 local runtime registration
     (OUTSIDE Git -- argine.research2.store.set/get_runtime_registration);
  3. a valid (capability-checked) ARGINE_LLAMA_DIR;
  4. the repository-documented canonical Research Part 1 runtime
     (identity verified by exact SHA-256, like trace_source.py's
     DOCUMENTED_CANONICAL_EXP019 -- never trusted merely by path/name);
  5. narrow discovery among the approved local roots (never an
     uncontrolled whole-disk scan): exactly one capability-eligible
     candidate resolves; zero -> RUNTIME_NOT_FOUND; two or more -> never
     silently pick -- RUNTIME_AMBIGUOUS.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from .. import runtime as rtmod
from . import provenance, store

RUNTIME_RESOLVED = "RUNTIME_RESOLVED"
RUNTIME_NOT_FOUND = "RUNTIME_NOT_FOUND"
RUNTIME_AMBIGUOUS = "RUNTIME_AMBIGUOUS"

# The llama-server.exe this repository's own documentation
# (experiments/EXP-019-instrumentation-validation/README.md and
# llama-research-v2/BUILD-INFO.json + VALIDATIONS.jsonl, all in the
# sibling Argine2 repository) identifies as the one actually used for the
# publication-quality Windows validation run: binary llama-research-v2,
# SHA-256 a0063720... (VALIDATIONS.jsonl validation_run_id prefix
# "20261012T072226Z-a0063720-run00", publication_quality true). A copy is
# "the documented canonical runtime" ONLY if its SHA-256 matches exactly
# -- never merely because it sits at one of these conventional paths
# (HoPi bundles a byte-identical copy of this same binary for its own
# packaged runtime; Argine2 also has llama-noomp/llama-patched/
# llama-research (v1), which are real, expert-store-capable, but are
# DIFFERENT builds and must never be silently treated as this one).
DOCUMENTED_CANONICAL_RUNTIME = {
    "identity": "llama-research-v2 (Argine2 EXP-019/EXP-021 research binary)",
    "expected_sha256": "a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3",
    # Checked in this order; first file that exists AND hash-matches wins.
    # All relative to this repository's own parent directory (i.e. the
    # approved sibling-project roots), never an absolute machine path.
    "candidate_relative_paths": (
        "Argine2/llama-research-v2/llama-server.exe",
        "HoPi/native/argine_engine/bin/llama-server.exe",
        "HoPi/dist/HoPi/_internal/native/argine_engine/bin/llama-server.exe",
    ),
}

# Narrow discovery roots (section 3: "do not scan the entire disk"). Project
# siblings only, by name, relative to this repository's own parent
# directory -- never a user-specific absolute path, and never the rest of
# the filesystem.
APPROVED_SIBLING_ROOTS = ("Argine", "Argine2", "Argine3", "HoPi")

# Bounded subpaths actually walked under each approved root (never an
# unbounded recursive scan -- HoPi in particular contains venvs, build
# caches and a frozen PyInstaller `dist/` tree that are enormous and
# irrelevant). Each is relative to the root itself.
_NARROW_SUBPATHS = (
    "*/llama-server.exe",                                   # Argine2/<build>/
    "llama-server.exe",                                     # a root's own top level
    "native/argine_engine/bin/llama-server.exe",             # HoPi dev tree
    "dist/HoPi/_internal/native/argine_engine/bin/llama-server.exe",  # HoPi frozen build
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _capability_check(path: "str | Path") -> "tuple[bool, str]":
    """Reuses argine.runtime's OWN existing capability validation --
    LlamaRuntime.supports_expert_store() (the ARGINE_EXPERT_STORE binary
    marker check) -- never a new/invented capability definition. A stock,
    unpatched llama-server.exe must fail this."""
    p = Path(path)
    if not p.is_file():
        return False, f"not a file: {p}"
    rt = rtmod.LlamaRuntime(root=p.parent, backend="cpu", build="?")
    if not rt.has_binary("server"):
        return False, f"no llama-server binary resolvable under {p.parent}"
    if not rt.supports_expert_store():
        return False, ("llama-server.exe does not contain the ARGINE_EXPERT_STORE "
                       "marker -- it is a stock/unpatched (or wrongly-built) binary; "
                       "the expert-store instrumentation `argine research trace` "
                       "needs would be silently ignored")
    return True, "expert-store marker present"


@dataclass
class RuntimeCandidate:
    path: str
    capability_ok: bool
    reason: str


@dataclass
class RuntimeResolution:
    status: str  # RUNTIME_RESOLVED | RUNTIME_NOT_FOUND | RUNTIME_AMBIGUOUS
    resolved_path: Optional[str] = None
    tier: Optional[str] = None
    candidates: List[RuntimeCandidate] = field(default_factory=list)
    stale_env_note: Optional[str] = None
    message: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        return d


def _documented_canonical_path() -> Optional[Path]:
    """The first documented-canonical candidate location that exists AND
    whose SHA-256 matches exactly, or None. Never returns a path based on
    name/location alone."""
    base = _repo_root().parent
    expected = DOCUMENTED_CANONICAL_RUNTIME["expected_sha256"]
    for rel in DOCUMENTED_CANONICAL_RUNTIME["candidate_relative_paths"]:
        p = base / rel
        if p.is_file():
            try:
                if _sha256_file(p) == expected:
                    return p
            except OSError:
                continue
    return None


def discover_local_candidates(search_roots: Optional[List[Path]] = None
                              ) -> List[RuntimeCandidate]:
    """Narrow, bounded discovery (section 3: never an uncontrolled
    whole-disk scan) among the approved project-sibling roots. Reports
    every real llama-server.exe found, with its own capability status --
    never used to silently auto-select; resolve() alone decides what that
    means."""
    base = _repo_root().parent
    roots = search_roots if search_roots is not None else [
        base / name for name in APPROVED_SIBLING_ROOTS if (base / name).is_dir()
    ]
    found: List[Path] = []
    seen: set = set()
    for root in roots:
        if not root.is_dir():
            continue
        for sub in _NARROW_SUBPATHS:
            for p in root.glob(sub):
                if p.is_file():
                    rp = p.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        found.append(p)
    out: List[RuntimeCandidate] = []
    for p in sorted(found, key=str):
        ok, reason = _capability_check(p)
        out.append(RuntimeCandidate(path=str(p), capability_ok=ok, reason=reason))
    return out


def register_runtime(path: "str | Path", *, resolution_source: str) -> dict:
    """Build (never silently trust) a registration record for a runtime
    this resolver established as the one to use. Hashes the binary --
    section 7: SHA-256 is computed only at explicit registration time,
    never on every status/doctor call. Never written by this function
    itself; callers persist via argine.research2.store.set_runtime_registration."""
    p = Path(path)
    ok, reason = _capability_check(p)
    if not ok:
        raise ValueError(f"refusing to register a runtime that fails capability "
                         f"validation: {p} ({reason})")
    return {
        "path": str(p),
        "size_bytes": p.stat().st_size,
        "sha256": _sha256_file(p),
        "capability_status": reason,
        "resolution_source": resolution_source,
        "registered_at_utc": provenance.utc_now_iso(),
    }


def resolve(*, explicit_runtime: Optional[str] = None,
           search_roots: Optional[List[Path]] = None) -> RuntimeResolution:
    # Tier 1: explicit override. An explicit choice that fails validation
    # is reported as-is rather than silently substituted with something
    # else -- the one tier where falling through would be more surprising
    # than refusing.
    if explicit_runtime:
        p = Path(explicit_runtime)
        if not p.is_file():
            return RuntimeResolution(status=RUNTIME_NOT_FOUND, tier="cli_explicit",
                                     message=f"--runtime path does not exist: {p}")
        ok, reason = _capability_check(p)
        if not ok:
            return RuntimeResolution(status=RUNTIME_NOT_FOUND, tier="cli_explicit",
                                     message=f"--runtime {p} failed capability "
                                             f"validation: {reason}")
        return RuntimeResolution(status=RUNTIME_RESOLVED, resolved_path=str(p),
                                 tier="cli_explicit",
                                 message=f"using explicitly provided runtime: {p}")

    # Tier 2: a previously-verified Research2 local registration. Never
    # trusted blindly -- re-validated every time, because the file it
    # points at can move or be deleted after registration.
    registered = store.get_runtime_registration()
    if registered and registered.get("path"):
        p = Path(registered["path"])
        ok, reason = _capability_check(p)
        if ok:
            return RuntimeResolution(status=RUNTIME_RESOLVED, resolved_path=str(p),
                                     tier="registered",
                                     message=f"using Research2-registered runtime: {p}")
        # registration is stale (moved/deleted/no-longer-capable): fall
        # through to the remaining tiers rather than failing closed on a
        # cache that no longer reflects reality.

    # Tier 3: ARGINE_LLAMA_DIR, if valid. Never mutated here. An
    # invalid/stale value is recorded as a note and resolution continues --
    # section 5: a stale env var must not defeat a stronger fallback.
    env_value, env_var, env_dir_installed = rtmod.llama_dir_configurato()
    stale_env_note: Optional[str] = None
    if env_value:
        if env_dir_installed:
            root = Path(env_value).expanduser()
            server = (root / "llama-server.exe") if (root / "llama-server.exe").is_file() \
                else next(iter(root.rglob("llama-server.exe")), None)
            ok, reason = (_capability_check(server) if server else
                         (False, "llama-server not found under this directory"))
            if ok:
                return RuntimeResolution(status=RUNTIME_RESOLVED, resolved_path=str(server),
                                         tier="argine_llama_dir",
                                         message=f"using {env_var}={env_value} "
                                                 f"(capability-validated)")
            stale_env_note = (f"{env_var}={env_value} contains a llama-server binary but "
                              f"it failed capability validation: {reason}")
        else:
            stale_env_note = (f"{env_var}={env_value} does not contain llama-server "
                              f"(stale/invalid -- continuing deterministic resolution)")

    # Tier 4: repository-documented canonical runtime, identity-verified
    # by exact SHA-256 (never by path/name alone).
    canon = _documented_canonical_path()
    if canon is not None:
        ok, reason = _capability_check(canon)
        if ok:
            return RuntimeResolution(status=RUNTIME_RESOLVED, resolved_path=str(canon),
                                     tier="documented_canonical", stale_env_note=stale_env_note,
                                     message=f"using repository-documented canonical "
                                             f"runtime ({DOCUMENTED_CANONICAL_RUNTIME['identity']}): "
                                             f"{canon}")

    # Tier 5: narrow discovery among approved sibling roots. Zero/multiple
    # eligible candidates are two distinct, never-conflated outcomes (the
    # same fix applied to trace_source.resolve()'s TRACE_SOURCE_* split).
    candidates = discover_local_candidates(search_roots)
    eligible = [c for c in candidates if c.capability_ok]
    if len(eligible) == 1:
        return RuntimeResolution(status=RUNTIME_RESOLVED, resolved_path=eligible[0].path,
                                 tier="narrow_discovery", candidates=candidates,
                                 stale_env_note=stale_env_note,
                                 message=f"exactly one capability-eligible local runtime "
                                         f"candidate: {eligible[0].path}")
    if not eligible:
        if not candidates:
            message = ("no local llama-server.exe found under the approved project "
                      "roots (Argine, Argine2, Argine3, HoPi). Pass --runtime PATH, "
                      "set ARGINE_LLAMA_DIR, or run 'argine setup'.")
        else:
            message = (f"{len(candidates)} local llama-server.exe candidate(s) found, "
                      f"0 pass capability validation (expert-store marker absent -- "
                      f"stock/unpatched binaries). Pass --runtime PATH naming a "
                      f"patched build, or run 'argine setup'.")
        return RuntimeResolution(status=RUNTIME_NOT_FOUND, candidates=candidates,
                                 stale_env_note=stale_env_note, message=message)
    return RuntimeResolution(
        status=RUNTIME_AMBIGUOUS, candidates=candidates, stale_env_note=stale_env_note,
        message=f"{len(eligible)} capability-eligible runtime candidate(s) found "
                f"({len(candidates)} candidate(s) discovered in total) and no "
                f"deterministic canonical choice can be made among them. "
                f"Pass --runtime PATH naming the one to use.")
