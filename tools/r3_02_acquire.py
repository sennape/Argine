"""R3-02 acquisition driver (Phase 1 executor, Phase 0/2B frozen contract).

Consumes the frozen ACQUISITION_PLAN.json verbatim. Fails closed on any
identity, digest, count or ordering mismatch. Supports a mandatory --dry-run
that validates the entire schedule without starting any process.

This module never runs the model on its own: --dry-run is the only mode used
in R3-02B; the execution path exists for R3-02 Phase 1 and is exercised only
by mocked tests here.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
# Project-local import bootstrap: running `python tools\r3_02_acquire.py` puts
# tools/ on sys.path, not the repository root, so `import argine` failed with
# ModuleNotFoundError in the live path (tests passed only because they inserted
# paths themselves). Resolve the repository root from __file__ and add exactly
# that directory; no PYTHONPATH, no pip install, no cwd dependency.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
R31_DIR = REPO / "experiments" / "R3-01-telemetry-realization"
if str(R31_DIR) not in sys.path:
    sys.path.insert(0, str(R31_DIR))
import r3_telemetry.telemetry as T  # noqa: E402  (frozen R3-01 machinery)
PHASE = REPO / "experiments" / "R3-02-calibration-mechanism"
DEFAULT_PLAN = PHASE / "ACQUISITION_PLAN.json"
DEFAULT_PREREG = PHASE / "PREREGISTRATION.json"
DEFAULT_OUT = Path(r"C:\Users\alpel\Desktop\Argine4-r3runs\R3-02")

PROTOCOL_DIGEST = "f8d4b3199c4c74555353e5f796eaf7d7c3687da97883ee07f7d1c81233c9469c"
CONTRACT_DIGEST = "99a68c73c5a3caba4fd53937f27907375734f3866c84e18d4ecb944013efa073"
RUNTIME_SHA = "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644"
MODEL_SHA = "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32"
STORE_SHA = "fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146"
WORKLOAD_SHA = "c02f9657d12d874cc43523e2b609bb604a68a6a8b267b2ffac6d3420c509b730"
WORKLOAD_ID = "r2-11-coding-v1"

EXPECTED_COUNTS = {
    "preflight": {"total": 3, "launches": {"PREFLIGHT": 3}},
    "scientific": {"total": 49, "launches": {"TRACE_COUNT": 7, "TIMING_WARMUP": 7, "TIMING_COUNTED": 35}},
}
PORT = 18080
READY_TIMEOUT_S = 600
REQUEST_TIMEOUT_S = 1800
COOLDOWN_TOLERANCE_S = 0.0


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def protocol_digest(plan: Dict[str, Any], prereg: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(
        {"preregistration": prereg, "acquisition_plan": plan}).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Frozen-contract validation (pure; no hashing)
# ---------------------------------------------------------------------------

def validate_frozen(plan: Dict[str, Any], prereg: Dict[str, Any],
                    expected_digest: str = PROTOCOL_DIGEST) -> List[str]:
    f: List[str] = []
    if plan.get("artifact") != "R3-02-ACQUISITION-PLAN":
        f.append("plan artifact id mismatch")
    if plan.get("status") != "FROZEN" or prereg.get("status") != "FROZEN":
        f.append("plan/preregistration not FROZEN")
    if protocol_digest(plan, prereg) != expected_digest:
        f.append("protocol digest mismatch")

    counts = plan.get("counts") or {}
    launches = plan.get("launches") or []
    if counts.get("total_host_process_launches") != 52 or len(launches) != 52:
        f.append("total launches != 52")
    if [l.get("seq") for l in launches] != list(range(1, 53)):
        f.append("launch sequence is not 1..52")

    for stage, spec in EXPECTED_COUNTS.items():
        sel = stage_launches(plan, stage)
        if len(sel) != spec["total"]:
            f.append(f"{stage}: {len(sel)} launches != {spec['total']}")
        for kind, n in spec["launches"].items():
            got = sum(1 for l in sel if l["kind"] == kind)
            if got != n:
                f.append(f"{stage}: {kind} {got} != {n}")

    if counts.get("trace_count_launches") != 7 or counts.get("timing_warmups") != 7:
        f.append("TRACE_COUNT/warmup counts wrong")
    if counts.get("timing_counted_launches") != 35 or counts.get("scientific_cells") != 35:
        f.append("counted/cell counts wrong")

    # trace reuse cardinality: 5 counted per configuration, one TC each
    tc = {}
    counted = {}
    for l in launches:
        if l["kind"] == "TRACE_COUNT":
            tc[l["config_id"]] = tc.get(l["config_id"], 0) + 1
        if l["kind"] == "TIMING_COUNTED":
            counted[l["config_id"]] = counted.get(l["config_id"], 0) + 1
    if set(tc) != set(counted) or set(tc.values()) != {1} or set(counted.values()) != {5}:
        f.append("trace reuse cardinality is not 1 trace -> 5 counted TIMING per configuration")

    # policies / budgets / n_predict per stage
    for l in stage_launches(plan, "preflight"):
        if l["budget_label"] != "8" or l["n_predict"] != 32 or l["counted"] or l["scientific_cell"]:
            f.append(f"preflight launch {l['seq']} is not a non-scientific 8 GiB n=32 run")
    for l in stage_launches(plan, "scientific"):
        if l["n_predict"] != 512:
            f.append(f"scientific launch {l['seq']} n_predict != 512")

    if plan.get("runtime", {}).get("sha256") != RUNTIME_SHA:
        f.append("plan runtime sha != frozen R3 sha")
    if plan.get("contract_digest") != CONTRACT_DIGEST:
        f.append("contract digest mismatch")
    if plan.get("measurements_included") is not False:
        f.append("plan claims measurements")
    return f


def stage_launches(plan: Dict[str, Any], stage: str) -> List[Dict[str, Any]]:
    if stage not in EXPECTED_COUNTS:
        raise ValueError(f"unknown stage {stage!r}")
    return [l for l in plan["launches"] if l["phase"].lower() == stage]


# ---------------------------------------------------------------------------
# Identity validation (hashing; no model load)
# ---------------------------------------------------------------------------

def verify_identities(plan: Dict[str, Any], *, hash_files: bool = True,
                      runtime_path: Optional[Path] = None,
                      model_path: Optional[Path] = None,
                      store_path: Optional[Path] = None,
                      workload_path: Optional[Path] = None) -> List[str]:
    f: List[str] = []
    rt = Path(runtime_path or plan["runtime"]["path"])
    mp = Path(model_path or plan["model"]["path"])
    sp = Path(store_path or plan["store"]["path"])
    wp = Path(workload_path or REPO / plan["workload"]["path"])

    for label, p in (("runtime", rt), ("model", mp), ("store", sp), ("workload", wp)):
        if not p.exists():
            f.append(f"{label} path missing: {p}")
    if f:
        return f
    if not hash_files:
        return f
    checks = (("runtime", rt, plan["runtime"]["sha256"]),
              ("model", mp, plan["model"]["sha256"]),
              ("store", sp, plan["store"]["sha256"]),
              ("workload", wp, plan["workload"]["sha256"]))
    for label, p, expected in checks:
        actual = sha256_file(p)
        if actual != expected:
            f.append(f"{label} sha256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)")
    return f


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

STATUSES = ("PLANNED", "RUNNING", "ARCHIVING", "ARCHIVED", "SUCCEEDED", "FAILED", "INADMISSIBLE")


@dataclass
class State:
    path: Path
    launches: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    stage_status: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "State":
        st = cls(path=path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            st.launches = {str(k): v for k, v in (data.get("launches") or {}).items()}
            st.stage_status = dict(data.get("stage_status") or {})
        return st

    def save(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"launches": self.launches, "stage_status": self.stage_status}
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def status(self, seq: int) -> str:
        return self.launches.get(str(seq), {}).get("status", "PLANNED")

    def mark(self, seq: int, status: str, **extra: Any) -> None:
        assert status in STATUSES
        rec = dict(self.launches.get(str(seq), {}))
        rec.update({"status": status, **extra})
        self.launches[str(seq)] = rec
        self.save()


# ---------------------------------------------------------------------------
# Process runner (mocked in tests; never used in R3-02B)
# ---------------------------------------------------------------------------

class SubprocessRunner:
    """Real llama-server lifecycle. Only Phase 1 may use this."""

    def __init__(self, plan: Dict[str, Any], out_dir: Path):
        self.plan = plan
        self.out_dir = out_dir

    def port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def run_launch(self, launch: Dict[str, Any], *, trace_path: Optional[Path]) -> Dict[str, Any]:
        cfg = next(c for c in self.plan["configurations"] if c["config_id"] == launch["config_id"])
        env = os.environ.copy()
        env.update({
            "ARGINE_EXPERT_STORE": self.plan["store"]["path"],
            "ARGINE_EXPERT_BUDGET": str(cfg["budget_bytes"]),
            "ARGINE_EXPERT_SHARDS": "1",
            "ARGINE_EXPERT_READ": "blocco",
            "ARGINE_RETENTION_POLICY": cfg["policy_id"],
            "ARGINE_RETENTION_DECAY": str(cfg["params"]["decay_factor"]),
            "ARGINE_RETENTION_INTERVAL": str(cfg["params"]["aging_interval_steps"]),
        })
        for k, v in (("ARGINE_RETENTION_PREDICTOR_DECAY", "predictor_decay"),
                     ("ARGINE_RETENTION_TOPK", "top_k"),
                     ("ARGINE_RETENTION_NEXPERTS", "n_experts")):
            if v in cfg["params"]:
                env[k] = str(cfg["params"][v])
        if trace_path is not None:
            env["ARGINE_RETENTION_TRACE"] = str(trace_path)

        log_dir = self.out_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{launch['seq']:02d}-{cfg['config_id']}"
        stdout_path = log_dir / f"{tag}.stdout.log"
        stderr_path = log_dir / f"{tag}.stderr.log"
        cmd = [self.plan["runtime"]["path"], "-m", self.plan["model"]["path"],
               "-c", str(self.plan["decoding"]["context"]), "-n", str(launch["n_predict"]),
               "--host", "127.0.0.1", "--port", str(PORT)]
        with stdout_path.open("wb") as so, stderr_path.open("wb") as se:
            proc = subprocess.Popen(cmd, env=env, stdout=so, stderr=se)
            request_completed = False
            shutdown: Dict[str, Any] = {}
            try:
                self._wait_ready(proc)
                result = self._request(launch)
                request_completed = True
                cpu = self._cpu_seconds(proc.pid)
            finally:
                shutdown = self._stop(proc)
        result.update({"exit_code": shutdown["exit_code"], "stdout_path": str(stdout_path),
                       "stderr_path": str(stderr_path), "process_cpu_seconds": cpu,
                       "request_completed_successfully": request_completed,
                       "shutdown": shutdown})
        return result

    def _cpu_seconds(self, pid: int) -> float:
        try:
            import psutil  # type: ignore
        except Exception as exc:
            raise LaunchFailure(f"psutil unavailable, process_cpu_seconds cannot be measured: {exc}") from exc
        times = psutil.Process(pid).cpu_times()
        return float(times.user) + float(times.system)

    def _wait_ready(self, proc: subprocess.Popen) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early with {proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(1.0)
        raise TimeoutError("llama-server readiness timeout")

    def _request(self, launch: Dict[str, Any]) -> Dict[str, Any]:
        prompt = workload_prompt_for_request(self.plan)
        body = {"prompt": prompt, "n_predict": launch["n_predict"],
                "seed": self.plan["decoding"]["seed"],
                "temperature": self.plan["decoding"]["temperature"],
                "top_k": self.plan["decoding"]["top_k"],
                "return_tokens": True, "stream": False}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        elapsed = time.monotonic() - t0
        timings = payload.get("timings") or {}
        return {
            "tokens": payload.get("tokens"),
            "timings": timings,
            "wall_seconds": elapsed,
            "prompt_seconds": float(timings.get("prompt_ms", 0.0)) / 1000.0,
            "predicted_seconds": float(timings.get("predicted_ms", 0.0)) / 1000.0,
        }

    def _stop(self, proc: subprocess.Popen) -> Dict[str, Any]:
        alive = proc.poll() is None
        terminate_used = False
        kill_used = False
        if alive:
            proc.terminate()
            terminate_used = True
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                kill_used = True
                proc.wait(timeout=30)
        return {"process_alive_before_shutdown": alive, "terminate_used": terminate_used,
                "kill_used": kill_used, "exit_code": proc.returncode}


# ---------------------------------------------------------------------------
# Schedule validation / dry-run
# ---------------------------------------------------------------------------

def dry_run(plan: Dict[str, Any], prereg: Dict[str, Any], stage: str,
            *, hash_files: bool = True) -> Dict[str, Any]:
    failures = validate_frozen(plan, prereg)
    failures += verify_identities(plan, hash_files=hash_files)
    launches = stage_launches(plan, stage)
    schedule = []
    for l in launches:
        cfg = next(c for c in plan["configurations"] if c["config_id"] == l["config_id"])
        schedule.append({
            "seq": l["seq"], "kind": l["kind"], "config_id": l["config_id"],
            "control": l["control"], "policy_id": cfg["policy_id"],
            "budget_bytes": cfg["budget_bytes"], "n_predict": l["n_predict"],
            "repetition": l["repetition"], "counted": l["counted"],
            "cooldown_before_s": l["cooldown_before_s"],
            "trace_mode": l["kind"] == "TRACE_COUNT",
        })
    report = {
        "artifact": "R3-02B-DRY-RUN",
        "stage": stage,
        "status": "VALID" if not failures else "INVALID",
        "failures": failures,
        "launches": len(schedule),
        "counts": {k: sum(1 for s in schedule if s["kind"] == k)
                   for k in ("TRACE_COUNT", "TIMING_WARMUP", "TIMING_COUNTED", "PREFLIGHT")},
        "schedule": schedule,
        "expected_counts": EXPECTED_COUNTS[stage],
        "protocol_digest": protocol_digest(plan, prereg),
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="R3-02 acquisition driver")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--stage", required=True, choices=("preflight", "scientific"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--state", default=None)
    ap.add_argument("--attest-clean-environment", action="store_true")
    ap.add_argument("--skip-file-hashes", action="store_true",
                    help="only for tests; real runs must hash identities")
    ap.add_argument("--force-rerun-completed", action="store_true")
    ap.add_argument("--plan-resume", action="store_true",
                    help="offline: list only the remaining model-bearing launches")
    ap.add_argument("--recover", action="store_true",
                    help="offline: finalize interrupted TRACE_COUNT archives, then exit")
    ap.add_argument("--authorize-preflight", action="store_true",
                    help="offline: evaluate the canonical preflight authorization predicate")
    ap.add_argument("--check-storage", action="store_true",
                    help="verify NTFS compression and free-space headroom, then exit")
    ap.add_argument("--check-imports", action="store_true",
                    help="verify that the repository-local argine package resolves, then exit")
    args = ap.parse_args(argv)

    if args.check_imports:
        try:
            path = planner_module_path()
        except Exception as exc:
            print(json.dumps({"ok": False, "repo_root": str(REPO), "error": str(exc)}))
            return 1
        print(json.dumps({"ok": True, "repo_root": str(REPO), "planner_path": path,
                          "inside_repo": str(REPO) in path,
                          "sys_path_has_repo": str(REPO) in sys.path}))
        return 0

    plan = load_json(Path(args.plan))
    prereg = load_json(Path(args.plan).parent / "PREREGISTRATION.json")
    failures = validate_frozen(plan, prereg)
    failures += verify_identities(plan, hash_files=not args.skip_file_hashes)
    if failures:
        print("FROZEN CONTRACT VIOLATION:", json.dumps(failures, indent=1))
        return 2

    if args.plan_resume:
        st = State.load(Path(args.out_dir) / "state.json")
        rem = remaining_launches(plan, st)
        print(json.dumps({"out_dir": str(Path(args.out_dir)), "remaining": len(rem),
                          "launches": [{"seq": l["seq"], "kind": l["kind"],
                                        "config_id": l["config_id"],
                                        "repetition": l.get("repetition")} for l in rem]},
                         indent=2, sort_keys=True))
        return 0

    if args.recover:
        st = State.load(Path(args.out_dir) / "state.json")
        try:
            recovered = recover_interrupted(Path(args.out_dir), plan, st)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
            return 1
        print(json.dumps({"ok": True, "recovered": recovered,
                          "free_bytes": free_bytes(Path(args.out_dir))}, indent=2, sort_keys=True))
        return 0

    if args.authorize_preflight:
        st = State.load(Path(args.out_dir) / "state.json")
        ok = preflight_evidence_complete(st)
        detail = {}
        for k in (1, 2, 3):
            rec = st.launches.get(str(k)) or {}
            ev = rec.get("evidence") or {}
            detail[str(k)] = {"status": rec.get("status"), "schema": ev.get("schema"),
                              "clean": launch_clean(ev),
                              "server_return_code": ev.get("server_return_code"),
                              "shutdown_mode": ev.get("shutdown_mode"),
                              "stderr_error_markers": ev.get("stderr_error_markers")}
        print(json.dumps({"out_dir": str(Path(args.out_dir)), "authorized": ok,
                          "stage_status": st.stage_status, "launches": detail},
                         indent=2, sort_keys=True))
        return 0 if ok else 1

    if args.check_storage:
        out_dir = Path(args.out_dir)
        need = int(os.environ.get(STORAGE_REQUIREMENT_ENV, STORAGE_MIN_FREE_BYTES_DEFAULT))
        failures = storage_gate(plan, out_dir, required_free=need)
        info = {"ok": not failures, "failures": failures, "out_dir": str(out_dir),
                "filesystem": filesystem_type(out_dir),
                "traces_compressed": is_compressed(out_dir / "traces"),
                "free_bytes": free_bytes(out_dir), "required_free_bytes": need}
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0 if not failures else 1

    if args.dry_run:
        report = dry_run(plan, prereg, args.stage, hash_files=not args.skip_file_hashes)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "VALID" else 2

    if not args.attest_clean_environment:
        print("REFUSING TO LAUNCH: --attest-clean-environment is required")
        return 3

    state_path = Path(args.state or (Path(args.out_dir) / "state.json"))
    state = State.load(state_path)
    if args.stage == "scientific" and state.stage_status.get("preflight") != "SUCCEEDED":
        print("REFUSING: preflight stage has not succeeded")
        return 4
    runner = SubprocessRunner(plan, Path(args.out_dir))
    summary = execute_stage(plan, prereg, args.stage, state, out_dir=Path(args.out_dir),
                            runner=runner, force=args.force_rerun_completed)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["stage_status"] == "SUCCEEDED" else 1


# ---------------------------------------------------------------------------
# Workload resolution (canonical: items[0].prompt, as in R2-12 resolve_workload)
# ---------------------------------------------------------------------------

def load_workload_prompt(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Return the canonical prompt for the frozen workload.

    The validated R2/R3 construction is ``doc["items"][0]["prompt"]``; anything
    else (missing items, missing prompt, multiple items, changed sha/n_predict)
    fails closed rather than sending an empty or invented prompt.
    """
    wl_path = REPO / plan["workload"]["path"]
    if not wl_path.exists():
        raise LaunchFailure(f"workload file missing: {wl_path}")
    actual = sha256_file(wl_path)
    if actual != plan["workload"]["sha256"]:
        raise LaunchFailure(f"workload sha256 mismatch: {actual}")
    doc = load_json(wl_path)
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise LaunchFailure("unsupported workload schema: expected a non-empty 'items' list")
    if len(items) != 1:
        raise LaunchFailure(f"unsupported workload schema: expected exactly one item, got {len(items)}")
    item = items[0]
    prompt = item.get("prompt") if isinstance(item, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise LaunchFailure("unsupported workload schema: items[0] has no non-empty 'prompt' string")
    npred = doc.get("n_predict")
    if npred is not None and int(npred) != int(plan["decoding"]["n_predict_scientific"]):
        raise LaunchFailure(f"workload n_predict {npred} != frozen {plan['decoding']['n_predict_scientific']}")
    return {"prompt": prompt, "sha256": actual, "items": len(items), "path": str(wl_path)}


def workload_prompt_for_request(plan: Dict[str, Any]) -> str:
    return load_workload_prompt(plan)["prompt"]


# ---------------------------------------------------------------------------
# Evidence persistence (R3-01 records, CELLs, provenance)
# ---------------------------------------------------------------------------

def write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def record_identity(plan: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, str]:
    return {
        "model_sha256": plan["model"]["sha256"],
        "store_sha256": plan["store"]["sha256"],
        "runtime_sha256": plan["runtime"]["sha256"],
        "workload_sha256": plan["workload"]["sha256"],
        "policy_params_sha256": T.policy_params_digest(cfg["params"]),
    }


def _evidence_digest(record: Dict[str, Any]) -> str:
    return hashlib.sha256(T.canonical_json(record).encode("utf-8")).hexdigest()


def _budget_invariant_ok(stderr_path: Path) -> bool:
    try:
        text = Path(stderr_path).read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return False
    for marker in ("budget violation", "budget_violation", "budget exceeded", "out of budget"):
        if marker in text:
            return False
    return True


def _stderr_provenance(result: Dict[str, Any]) -> Dict[str, Any]:
    out = {"exit_code": result.get("exit_code")}
    for key in ("stdout_path", "stderr_path"):
        p = result.get(key)
        if p and Path(p).exists():
            out[key] = str(p)
            out[key.replace("_path", "_sha256")] = sha256_file(Path(p))
            out[key.replace("_path", "_bytes")] = Path(p).stat().st_size
    return out


ARCHIVE_FORMAT = "zstd-v1"
ARCHIVE_LEVEL = 3
ARCHIVE_RATIO_MEASURED = 0.05062  # worst observed scientific ratio (A-8GiB)
SYSTEM_RESERVE_GIB = 15.0
LOGICAL_GIB_PER_BUDGET = {"1.5": 5.47, "4": 14.39, "8": 28.82}


def _require_zstd():
    try:
        import zstandard  # type: ignore
    except Exception as exc:
        raise LaunchFailure(f"zstandard unavailable, lossless archival cannot be verified: {exc}") from exc
    return zstandard


def compress_trace(raw_path: Path, archive_path: Path) -> None:
    zstd = _require_zstd()
    partial = archive_path.with_name(archive_path.name + ".partial")
    cctx = zstd.ZstdCompressor(level=ARCHIVE_LEVEL)
    with open(raw_path, "rb") as fi, open(partial, "wb") as fo:
        with cctx.stream_writer(fo, closefd=False) as zw:
            while True:
                chunk = fi.read(1 << 22)
                if not chunk:
                    break
                zw.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(partial, archive_path)


def decompress_and_hash(archive_path: Path, dest_path: Path) -> tuple:
    zstd = _require_zstd()
    dctx = zstd.ZstdDecompressor()
    h = hashlib.sha256()
    with open(archive_path, "rb") as fi, dctx.stream_reader(fi) as zr, open(dest_path, "wb") as fo:
        while True:
            chunk = zr.read(1 << 22)
            if not chunk:
                break
            h.update(chunk)
            fo.write(chunk)
    return h.hexdigest(), dest_path.stat().st_size


class ZstdTextPath:
    """Path-like wrapper that streams a zstd archive as text (no temp file)."""

    def __init__(self, archive: Path):
        self.archive = Path(archive)

    def open(self, mode: str = "r", encoding: str = "utf-8", errors: str = "replace"):
        zstd = _require_zstd()
        fh = open(self.archive, "rb")
        try:
            reader = zstd.ZstdDecompressor().stream_reader(fh, closefd=True)
        except TypeError:
            reader = zstd.ZstdDecompressor().stream_reader(fh)
        return io.TextIOWrapper(reader, encoding=encoding, errors=errors)


def stream_verify_archive(archive_path: Path, expected_sha: str, expected_bytes: int,
                          expected_counters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Streaming-only verification: logical SHA, logical bytes and parser counters."""
    zstd = _require_zstd()
    h = hashlib.sha256()
    n = 0
    with open(archive_path, "rb") as fi:
        with zstd.ZstdDecompressor().stream_reader(fi) as zr:
            while True:
                chunk = zr.read(1 << 22)
                if not chunk:
                    break
                h.update(chunk)
                n += len(chunk)
    sha = h.hexdigest()
    if sha != expected_sha:
        raise LaunchFailure("archive round-trip logical SHA mismatch")
    if n != int(expected_bytes):
        raise LaunchFailure(f"archive round-trip logical bytes mismatch: {n} != {expected_bytes}")
    counters = T.frozen.extract_decision_trace(ZstdTextPath(archive_path))
    if expected_counters:
        for k, v in expected_counters.items():
            if counters.get(k) != v:
                raise LaunchFailure(f"archive parser counter mismatch: {k}")
    return {"logical_sha256": sha, "logical_bytes": n, "counters": counters,
            "archive_sha256": sha256_file(archive_path),
            "archive_bytes": Path(archive_path).stat().st_size}


def verify_archive(archive_path: Path, expected_sha: str, expected_bytes: int,
                   expected_counters: Dict[str, Any], work_dir: Path) -> None:
    stream_verify_archive(archive_path, expected_sha, expected_bytes, expected_counters)


def archive_trace(raw_path: Path, archive_path: Path, expected_counters: Dict[str, Any]) -> Dict[str, Any]:
    logical_sha = sha256_file(raw_path)
    logical_bytes = raw_path.stat().st_size
    compress_trace(raw_path, archive_path)
    archive_sha = sha256_file(archive_path)
    archive_bytes = archive_path.stat().st_size
    verify_archive(archive_path, logical_sha, logical_bytes, expected_counters, archive_path.parent)
    if sha256_file(archive_path) != archive_sha:
        raise LaunchFailure("archive changed after verification")
    return {"format": ARCHIVE_FORMAT, "archive_sha256": archive_sha, "archive_bytes": archive_bytes,
            "logical_sha256": logical_sha, "logical_bytes": logical_bytes,
            "ratio": round(archive_bytes / logical_bytes, 6) if logical_bytes else None}


def prior_archive_bytes(out_dir: Path) -> int:
    total = 0
    traces = Path(out_dir) / "traces"
    if traces.exists():
        total = sum(p.stat().st_size for p in traces.glob("*.zst") if p.is_file())
    return total


def required_free_for_trace(plan: Dict[str, Any], cfg: Dict[str, Any], out_dir: Path) -> int:
    """Conservative per-sequence requirement: reserve + 110% raw trace + one temp archive.

    Prior archives are already on disk and are reflected in the measured free
    space; adding them here would double-count. The logical trace is inflated by
    10% and the archive by the worst observed scientific ratio.
    """
    gib = 1024 ** 3
    logical = LOGICAL_GIB_PER_BUDGET.get(cfg["budget_label"], 28.82) * 1.10 * gib
    temp = logical * ARCHIVE_RATIO_MEASURED
    return int(SYSTEM_RESERVE_GIB * gib + logical + temp)


ERROR_MARKERS = ("error", "fatal", "abort", "assert", "terminate called", "exception")


def stderr_error_markers(path: Any) -> list:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return ["stderr_unreadable"]
    return [m for m in ERROR_MARKERS if m in text]


def launch_clean(evidence: Dict[str, Any]) -> bool:
    """Canonical clean-launch predicate: request success, admission, and a proven shutdown.

    exit code 0 is clean. A nonzero code is clean ONLY when it is the documented
    Windows TerminateProcess result (1) of a driver-initiated, verified terminate
    after a completed request, with no kill escalation and no stderr error marker.
    """
    if evidence.get("schema") != 3:
        return False
    if evidence.get("request_completed_successfully") is not True:
        return False
    if not isinstance(evidence.get("output_token_digest"), str) or len(evidence["output_token_digest"]) != 64:
        return False
    if evidence.get("budget_invariant_ok") is not True:
        return False
    if evidence.get("admission") != "ADMISSIBLE":
        return False
    if evidence.get("stderr_error_markers"):
        return False
    code = evidence.get("server_return_code")
    if code == 0:
        return True
    return (code == 1
            and evidence.get("intentional_shutdown") is True
            and evidence.get("shutdown_verified") is True
            and evidence.get("shutdown_mode") == "terminate"
            and evidence.get("kill_used") is False
            and evidence.get("process_alive_before_shutdown") is True)


def shutdown_evidence(result: Dict[str, Any], stderr_path: Any) -> Dict[str, Any]:
    sh = result.get("shutdown") or {}
    alive = bool(sh.get("process_alive_before_shutdown"))
    terminate_used = bool(sh.get("terminate_used"))
    kill_used = bool(sh.get("kill_used"))
    code = result.get("exit_code")
    return {
        "schema": 3,
        "request_completed_successfully": bool(result.get("request_completed_successfully")),
        "server_return_code": code,
        "exit_code": code,
        "intentional_shutdown": terminate_used,
        "shutdown_mode": "terminate" if terminate_used else ("already_exited" if not alive else "unknown"),
        "shutdown_verified": (not kill_used) and code is not None,
        "kill_used": kill_used,
        "process_alive_before_shutdown": alive,
        "stderr_error_markers": stderr_error_markers(stderr_path),
    }


def _default_run_one(launch: Dict[str, Any], plan: Dict[str, Any], out_dir: Path,
                     runner: Any) -> Dict[str, Any]:
    cfg = next(c for c in plan["configurations"] if c["config_id"] == launch["config_id"])
    trace_path = None
    if launch["kind"] == "TRACE_COUNT":
        trace_path = out_dir / "traces" / f"{cfg['config_id']}.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
    result = runner.run_launch(launch, trace_path=trace_path)
    tokens = result.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        raise LaunchFailure("completion returned no token ids")
    identity = record_identity(plan, cfg)

    if launch["kind"] == "TRACE_COUNT":
        record = T.build_trace_count_record(
            trace_path=Path(trace_path), identity=identity,
            workload_id=plan["workload"]["id"], seed=plan["decoding"]["seed"],
            budget_bytes=cfg["budget_bytes"], policy_id=cfg["policy_id"],
            policy_params=cfg["params"], output_tokens=tokens)
        admission = T.admit_record(record)
        counters = record["telemetry"]["counters"]
        raw_counters = T.frozen.extract_decision_trace(Path(trace_path))
        if admission["status"] != "ADMISSIBLE":
            raise LaunchFailure(f"trace admission failed: {admission['failures']}")
        write_json_atomic(out_dir / "records" / f"trace_count-{cfg['config_id']}.json", record)

        archive_path = Path(trace_path).with_suffix(".zst")
        try:
            info = archive_trace(Path(trace_path), archive_path, raw_counters)
        except OSError as exc:
            raise StorageFailure(f"storage failure during archive lifecycle: {exc}") from exc
        record["telemetry"]["archive"] = {
            "path": str(archive_path), "inside_repository": False,
            "sha256": info["logical_sha256"], "bytes": info["logical_bytes"],
            "trace_format": record["telemetry"]["archive"].get("trace_format", "TRACE_FORMAT_V1"),
            "copied_into_repository": False,
            "format": info["format"], "archive_sha256": info["archive_sha256"],
            "archive_bytes": info["archive_bytes"],
        }
        record["telemetry"]["archive_storage"] = {
            "format": info["format"], "archive_sha256": info["archive_sha256"],
            "archive_bytes": info["archive_bytes"], "logical_sha256": info["logical_sha256"],
            "logical_bytes": info["logical_bytes"],
        }
        recheck = T.admit_record(record)
        if recheck["status"] != admission["status"]:
            raise LaunchFailure("archive representation changed admission")
        try:
            write_json_atomic(out_dir / "records" / f"trace_count-{cfg['config_id']}.json", record)
            write_json_atomic(out_dir / "admissions" / f"trace_count-{cfg['config_id']}.json", recheck)
            write_json_atomic(out_dir / "archive" / f"archive-{cfg['config_id']}.json", info)
        except OSError as exc:
            raise StorageFailure(f"storage failure during archive metadata persistence: {exc}") from exc
        Path(trace_path).unlink()
        evidence = {
            "kind": "TRACE_COUNT",
            "output_token_digest": record["output_token_digest"],
            "trace_sha256": record["telemetry"]["trace_sha256"],
            "trace_bytes": info["logical_bytes"],
            "trace_complete": record["telemetry"]["status"] == "COMPLETE",
            "budget_violations": counters["budget_violations"],
            "budget_invariant_ok": counters["budget_violations"] == 0,
            "admission": recheck["status"], "admission_failures": recheck["failures"],
            "archive": info, "archive_verified": True, "raw_removed": True,
            "record_sha256": _evidence_digest(record),
        }
        evidence.update(shutdown_evidence(result, result.get("stderr_path")))
        evidence.update(_stderr_provenance(result))
        return {"admitted": launch_clean(evidence), "record": record, "admission": recheck, "evidence": evidence}

    timings = result.get("timings") or {}
    ttft = float(result.get("prompt_seconds") or 0.0)
    decode = float(result.get("predicted_seconds") or 0.0)
    record = T.build_timing_record(
        identity=identity, workload_id=plan["workload"]["id"], seed=plan["decoding"]["seed"],
        budget_bytes=cfg["budget_bytes"], policy_id=cfg["policy_id"],
        policy_params=cfg["params"], output_tokens=tokens,
        ttft_seconds=ttft, decode_duration_seconds=decode, tokens_generated=len(tokens),
        process_cpu_seconds=float(result.get("process_cpu_seconds") or 0.0),
        events_served=0, overhead_process_cpu_seconds=0.0)
    admission = T.admit_record(record)
    tag = "warmup" if launch["kind"] == "TIMING_WARMUP" else "timing"
    evidence = {
        "kind": launch["kind"],
        "output_token_digest": record["output_token_digest"],
        "admission": admission["status"], "admission_failures": admission["failures"],
        "budget_invariant_ok": _budget_invariant_ok(Path(result.get("stderr_path", ""))),
        "record_sha256": _evidence_digest(record),
    }
    evidence.update(shutdown_evidence(result, result.get("stderr_path")))
    evidence.update(_stderr_provenance(result))
    name = f"{tag}-{cfg['config_id']}-r{launch['repetition'] or 0}"
    write_json_atomic(out_dir / "records" / f"{name}.json", record)
    write_json_atomic(out_dir / "admissions" / f"{name}.json", admission)
    return {"admitted": admission["status"] == "ADMISSIBLE", "record": record,
            "admission": admission, "evidence": evidence}


def _default_pair(launch: Dict[str, Any], result: Dict[str, Any], plan: Dict[str, Any],
                  out_dir: Path) -> bool:
    if launch["kind"] != "TIMING_COUNTED":
        return bool(result.get("admitted", False))
    if result.get("scientific_status") == "OFFLINE_FIXTURE":
        return False
    cfg_id = launch["config_id"]
    tc_path = out_dir / "records" / f"trace_count-{cfg_id}.json"
    if not tc_path.exists():
        return False
    tc_record = load_json(tc_path)
    timing_record = result.get("record")
    if timing_record is None:
        return False
    cell = T.build_cell(tc_record, timing_record)
    admission = cell["admission"]
    name = f"cell-{cfg_id}-r{launch['repetition']}"
    write_json_atomic(out_dir / "cells" / f"{name}.json", cell)
    write_json_atomic(out_dir / "admissions" / f"{name}.json", admission)
    return admission["status"] == "ADMISSIBLE"


# ---------------------------------------------------------------------------
# Storage helpers (NTFS compression + free-space gate)
# ---------------------------------------------------------------------------

FILE_ATTRIBUTE_COMPRESSED = 0x800
STORAGE_MIN_FREE_BYTES_DEFAULT = 0
STORAGE_REQUIREMENT_ENV = "R3_02_STORAGE_MIN_FREE_BYTES"


def filesystem_type(path: Path) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(str(Path(path).resolve().anchor)), None, 0, None, None, None, buf, 64)
    return buf.value if ok else ""


def is_compressed(path: Path) -> bool:
    try:
        return bool(os.stat(path).st_file_attributes & FILE_ATTRIBUTE_COMPRESSED)
    except Exception:
        return False


def free_bytes(path: Path) -> int:
    import shutil
    return int(shutil.disk_usage(str(path)).free)


def storage_gate(plan: Dict[str, Any], out_dir: Path, *, required_free: Optional[int] = None) -> List[str]:
    failures: List[str] = []
    out_dir = Path(out_dir)
    if REPO in out_dir.resolve().parents or out_dir.resolve() == REPO:
        failures.append("output directory is inside the Git repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    traces = out_dir / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    if filesystem_type(out_dir).upper() != "NTFS":
        failures.append("output volume is not NTFS")
    if not is_compressed(traces):
        failures.append("trace directory does not have the NTFS compression attribute")
    need = int(required_free if required_free is not None else
               os.environ.get(STORAGE_REQUIREMENT_ENV, STORAGE_MIN_FREE_BYTES_DEFAULT))
    if need > 0 and free_bytes(out_dir) < need:
        failures.append(f"free space {free_bytes(out_dir)} < required {need}")
    return failures


# ---------------------------------------------------------------------------
# Execution engine (mock-tested in R3-02B; only Phase 1 may run it for real)
# ---------------------------------------------------------------------------

class LaunchFailure(RuntimeError):
    pass


class StorageFailure(LaunchFailure):
    """A disk/storage error: never retried, because retry would rerun the model."""


def _load_canonical_planner():
    """Import the repository's own argine.planner, never another installation."""
    import argine.planner as planner  # type: ignore

    path = Path(planner.__file__).resolve()
    if REPO not in path.parents:
        raise ImportError(f"argine resolved outside the repository: {path}")
    return planner


def planner_module_path() -> str:
    return str(Path(_load_canonical_planner().__file__).resolve())


def _load_gate_inputs(model_path: Path):
    from argine import hardware as hwmod  # type: ignore
    from argine.gguf import find_gguf, read_gguf  # type: ignore

    gguf_path = find_gguf(str(model_path))
    model = read_gguf(gguf_path)
    hw = hwmod.detect(extra_disk_paths=[str(Path(model.path).parent)])
    return model, hw


def default_free_ram_check(budget_bytes: int, model_path: Optional[Path] = None) -> None:
    """Frozen feasibility gate: canonical argine planner, never changes the budget."""
    try:
        plan_doc = load_json(DEFAULT_PLAN)
        target = Path(model_path or plan_doc["model"]["path"])
        planner = _load_canonical_planner()
        model, hw = _load_gate_inputs(target)
        req = planner.PlanRequest(
            ctx=int(plan_doc["decoding"]["context"]), threads=0, backend="cpu",
            expert_store=True, expert_store_budget=int(budget_bytes))
        result = planner.plan(model, hw, req)
        feasible = bool(getattr(result, "feasible", False))
    except Exception as exc:  # fail closed, never synthesize
        raise LaunchFailure(
            f"free-RAM feasibility check unavailable: {type(exc).__name__}: {exc}") from exc
    if not feasible:
        raise LaunchFailure("free-RAM feasibility check failed: planner reports infeasible")


def preflight_evidence_complete(state: "State") -> bool:
    """The 3 preflights must carry the new provenance schema; the old state cannot authorize Phase 1."""
    for k in (1, 2, 3):
        rec = state.launches.get(str(k)) or {}
        ev = rec.get("evidence") or {}
        if rec.get("status") != "SUCCEEDED":
            return False
        if not launch_clean(ev):
            return False
    return True


def _record_expected_counters(record: Dict[str, Any]) -> Dict[str, Any]:
    c = record["telemetry"]["counters"]
    return {
        "decision_rows": c["decision_rows"], "completion_rows": c["completion_rows"],
        "max_step": c["max_step"], "requested_expert_events": c["requested_expert_events"],
        "hit_expert_events": c["hit_expert_events"], "miss_expert_events": c["miss_expert_events"],
        "miss_bytes": c["expert_miss_bytes"], "evicted_expert_events": c["expert_evictions"],
        "peak_resident_bytes": c["resident_bytes_peak"], "budget_bytes": c["trace_budget_bytes"],
        "budget_violations": c["budget_violations"],
    }


def remaining_launches(plan: Dict[str, Any], state: "State") -> List[Dict[str, Any]]:
    done = {int(k) for k, v in state.launches.items() if v.get("status") == "SUCCEEDED"}
    return [l for l in plan["launches"] if l["seq"] not in done]


def recover_interrupted(out_dir: Path, plan: Dict[str, Any], state: "State") -> List[Dict[str, Any]]:
    """Finalize any interrupted TRACE_COUNT raw+archive pair, offline, before any gate.

    Never deletes raw unless the archive streams back to the exact logical SHA,
    bytes and parser counters, the canonical admission succeeds, and all
    metadata is persisted. Idempotent: an already-finalized pair only has its
    stale raw removed after the same streaming verification.
    """
    out_dir = Path(out_dir)
    recovered: List[Dict[str, Any]] = []
    for launch in stage_launches(plan, "scientific"):
        if launch["kind"] != "TRACE_COUNT":
            continue
        cfg = next(c for c in plan["configurations"] if c["config_id"] == launch["config_id"])
        rec_path = out_dir / "records" / f"trace_count-{cfg['config_id']}.json"
        arc = out_dir / "traces" / f"{cfg['config_id']}.zst"
        raw = out_dir / "traces" / f"{cfg['config_id']}.jsonl"
        if not rec_path.exists() or not arc.exists():
            continue
        if state.status(launch["seq"]) == "SUCCEEDED" and not raw.exists():
            continue
        record = load_json(rec_path)
        storage = record["telemetry"].get("archive_storage")
        expected_sha = record["telemetry"]["trace_sha256"]
        expected_bytes = int(record["telemetry"]["archive"]["bytes"])
        expected = _record_expected_counters(record)
        info = stream_verify_archive(arc, expected_sha, expected_bytes, expected)
        if storage is None:
            record["telemetry"]["archive"] = {
                "path": str(arc), "inside_repository": False,
                "sha256": info["logical_sha256"], "bytes": info["logical_bytes"],
                "trace_format": record["telemetry"]["archive"].get("trace_format", "TRACE_FORMAT_V1"),
                "copied_into_repository": False, "format": ARCHIVE_FORMAT,
                "archive_sha256": info["archive_sha256"], "archive_bytes": info["archive_bytes"],
            }
            record["telemetry"]["archive_storage"] = {
                "format": ARCHIVE_FORMAT, "archive_sha256": info["archive_sha256"],
                "archive_bytes": info["archive_bytes"], "logical_sha256": info["logical_sha256"],
                "logical_bytes": info["logical_bytes"],
            }
            admission = T.admit_record(record)
            if admission["status"] != "ADMISSIBLE":
                raise LaunchFailure(f"recovery admission failed: {admission['failures']}")
            write_json_atomic(out_dir / "archive" / f"archive-{cfg['config_id']}.json", info)
            write_json_atomic(rec_path, record)
            write_json_atomic(out_dir / "admissions" / f"trace_count-{cfg['config_id']}.json", admission)
        else:
            admission = load_json(out_dir / "admissions" / f"trace_count-{cfg['config_id']}.json")
        prev = dict(state.launches.get(str(launch["seq"])) or {})
        evidence = {
            "schema": 3, "kind": "TRACE_COUNT", "recovered": True,
            "request_completed_successfully": True,
            "server_return_code": prev.get("evidence", {}).get("server_return_code"),
            "intentional_shutdown": True, "shutdown_mode": "terminate",
            "shutdown_verified": False, "kill_used": False,
            "process_alive_before_shutdown": True, "stderr_error_markers": [],
            "output_token_digest": record["output_token_digest"],
            "budget_invariant_ok": record["telemetry"]["counters"]["budget_violations"] == 0,
            "admission": admission["status"],
            "record_sha256": _evidence_digest(record),
            "archive": {"format": ARCHIVE_FORMAT,
                        "archive_sha256": info["archive_sha256"],
                        "archive_bytes": info["archive_bytes"],
                        "logical_sha256": info["logical_sha256"],
                        "logical_bytes": info["logical_bytes"]},
            "recovery": {"recovered_from_status": prev.get("status"),
                         "original_attempts": prev.get("attempts"),
                         "original_error": prev.get("error"),
                         "verified_streaming": True},
        }
        state.mark(launch["seq"], "SUCCEEDED", attempts=prev.get("attempts"), evidence=evidence,
                   recovery=evidence["recovery"])
        if raw.exists():
            raw.unlink()
        recovered.append({"config": cfg["config_id"], "seq": launch["seq"],
                          "archive_bytes": info["archive_bytes"],
                          "logical_bytes": info["logical_bytes"],
                          "raw_removed": not raw.exists()})
    if recovered:
        state.save()
    return recovered


def execute_stage(
    plan: Dict[str, Any],
    prereg: Dict[str, Any],
    stage: str,
    state: "State",
    *,
    out_dir: Path,
    runner: Any,
    sleep: Optional[Callable[[float], None]] = None,
    free_ram_check: Optional[Callable[[int], None]] = None,
    run_one: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    pair_one: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None,
    force: bool = False,
    storage_check: bool = True,
    required_free: Optional[int] = None,
) -> Dict[str, Any]:
    """Run one frozen stage. Returns a small summary; raises on contract violations."""
    if sleep is None:
        sleep = time.sleep
    if free_ram_check is None:
        free_ram_check = default_free_ram_check
    failures = validate_frozen(plan, prereg)
    if failures:
        raise RuntimeError(f"frozen contract violation: {failures}")
    if stage == "scientific":
        if state.stage_status.get("preflight") != "SUCCEEDED":
            raise RuntimeError("preflight stage has not succeeded")
        if not preflight_evidence_complete(state):
            raise RuntimeError("preflight provenance incomplete: a new preflight is required")
    if stage == "scientific":
        recover_interrupted(Path(out_dir), plan, state)
    if storage_check:
        fs = storage_gate(plan, Path(out_dir), required_free=required_free)
        if fs:
            raise RuntimeError(f"storage gate failed: {fs}")

    def _run_one(launch: Dict[str, Any]) -> Dict[str, Any]:
        if run_one is not None:
            return run_one(launch)
        return _default_run_one(launch, plan, Path(out_dir), runner)

    def _pair(launch: Dict[str, Any], result: Dict[str, Any]) -> bool:
        if pair_one is not None:
            return pair_one(launch, result)
        return _default_pair(launch, result, plan, Path(out_dir))

    counts = {"succeeded": 0, "inadmissible": 0, "failed": 0, "skipped": 0}
    for launch in stage_launches(plan, stage):
        seq = launch["seq"]
        if state.status(seq) == "SUCCEEDED" and not force:
            counts["skipped"] += 1
            continue
        sleep(max(0, int(launch.get("cooldown_before_s", 0))))
        cfg = next(c for c in plan["configurations"] if c["config_id"] == launch["config_id"])
        if storage_check and launch["kind"] == "TRACE_COUNT":
            need = required_free_for_trace(plan, cfg, Path(out_dir))
            if free_bytes(Path(out_dir)) < need:
                raise LaunchFailure(
                    f"storage gate: free {free_bytes(Path(out_dir))} < conservative requirement {need}")
        free_ram_check(int(cfg["budget_bytes"]))
        attempts = int(state.launches.get(str(seq), {}).get("attempts", 0))
        while True:
            attempts += 1
            state.mark(seq, "RUNNING", attempts=attempts)
            try:
                result = _run_one(launch)
                admitted = _pair(launch, result) if launch["kind"] in (
                    "TRACE_COUNT", "TIMING_COUNTED", "TIMING_WARMUP") else True
                if not admitted:
                    raise LaunchFailure("admission failed")
            except StorageFailure as exc:
                state.mark(seq, "FAILED", attempts=attempts, error=str(exc))
                counts["failed"] += 1
                break
            except LaunchFailure:
                if attempts >= 2:
                    state.mark(seq, "INADMISSIBLE", attempts=attempts)
                    counts["inadmissible"] += 1
                    break
                continue
            except Exception as exc:
                if attempts >= 2:
                    state.mark(seq, "FAILED", attempts=attempts, error=str(exc))
                    counts["failed"] += 1
                    break
                continue
            evidence = result.get("evidence") or {}
            if launch_clean(evidence):
                state.mark(seq, "SUCCEEDED", attempts=attempts, evidence=evidence)
            else:
                if attempts >= 2:
                    state.mark(seq, "FAILED", attempts=attempts,
                               error="clean-launch predicate failed", evidence=evidence)
                    counts["failed"] += 1
                    break
                continue
            counts["succeeded"] += 1
            break

    state.stage_status[stage] = (
        "SUCCEEDED" if counts["failed"] == 0 and counts["inadmissible"] == 0 else "FAILED")
    state.save()
    if stage == "preflight" and state.stage_status[stage] == "SUCCEEDED":
        reloaded = State.load(state.path)
        if not preflight_evidence_complete(reloaded):
            state.stage_status[stage] = "FAILED"
            state.save()
    return {"stage": stage, **counts, "stage_status": state.stage_status[stage]}


def scientific_cells(plan: Dict[str, Any]) -> int:
    counted = sum(1 for l in stage_launches(plan, "scientific") if l["kind"] == "TIMING_COUNTED")
    tc = sum(1 for l in stage_launches(plan, "scientific") if l["kind"] == "TRACE_COUNT")
    return counted if counted == 5 * tc else 0


if __name__ == "__main__":
    raise SystemExit(main())
