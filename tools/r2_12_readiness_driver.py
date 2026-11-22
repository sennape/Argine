"""R2-12 -- live-host runtime-readiness driver (NOT the execution driver).

Run this on the REAL Windows machine that will eventually host R2-12's
measured runtime campaign. This script performs ONLY the frozen
pre-run-machine-gate checks (execution_plan.json.pre_run_machine_gate) plus
the broader host-identity/capability checks R2-12 Phase 0 requires before
any measured run can begin. It is read-only with respect to science:

  * It does NOT invoke any of the 120 counted benchmark runs.
  * It does NOT execute any measured policy comparison.
  * It does NOT create or populate any runtime-result artifact.
  * It does NOT create R2_12_RUNTIME_OPENED.
  * It does NOT, by itself, mark R2_12_RUNTIME_READY -- that state
    transition is a separate, explicit decision made in a later commit
    AFTER this driver has been run for real on the target host and its
    report reviewed. A clean report from this script is necessary but not
    sufficient on its own.

Every scientific constant checked here (model/runtime identity, frozen
budgets, instrumentation fields, etc.) is READ from the frozen R2-12
Phase-0 artifacts under experiments/R2-12-runtime-validation/ -- nothing
is duplicated or re-typed by hand.

Usage (from an Anaconda Python prompt, cwd = repo root):

    python tools\\r2_12_readiness_driver.py --operator-attests-clean-environment

If `python` resolves to the Microsoft Store alias rather than your actual
Anaconda interpreter (the R2-11 9009 failure mode), invoke the real
interpreter explicitly:

    C:\\Users\\<you>\\anaconda3\\python.exe tools\\r2_12_readiness_driver.py --operator-attests-clean-environment

Exit code is 0 only if there are zero BLOCKING_FAILURE checks (ADVISORY
checks never block). The full structured report is written to
experiments/R2-12-runtime-validation/RUNTIME-READINESS-REPORT.json
(additive; overwritten on each run, never merged into a frozen artifact).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CONTRACT_DIR = REPO_ROOT / "experiments" / "R2-12-runtime-validation"
REPORT_PATH = CONTRACT_DIR / "RUNTIME-READINESS-REPORT.json"
READINESS_LOCK_PATH = REPO_ROOT / "tools" / ".r2_12_readiness_driver.lock"
# Same lock path the execution driver (tools/r2_12_execution_driver.py) will
# hold for the duration of the whole campaign -- checked here too, so a
# readiness probe run while a real campaign is mid-flight is refused.
EXECUTION_LOCK_PATH = REPO_ROOT / "tools" / ".r2_12_execution_driver.lock"

PASS = "PASS"
BLOCKING_FAILURE = "BLOCKING_FAILURE"
ADVISORY = "ADVISORY"

_VALID_STATUSES = {PASS, BLOCKING_FAILURE, ADVISORY}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid check status: {self.status!r}")


@dataclass
class ReadinessReport:
    checks: List[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, status=status, detail=detail))

    @property
    def blocking_failures(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status == BLOCKING_FAILURE]

    @property
    def advisories(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status == ADVISORY]

    @property
    def overall(self) -> str:
        return PASS if not self.blocking_failures else BLOCKING_FAILURE

    def to_json(self) -> dict:
        return {
            "overall": self.overall,
            "n_checks": len(self.checks),
            "n_pass": sum(1 for c in self.checks if c.status == PASS),
            "n_blocking_failure": len(self.blocking_failures),
            "n_advisory": len(self.advisories),
            "checks": [asdict(c) for c in self.checks],
        }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def free_ram_bytes() -> int:
    try:
        import psutil  # type: ignore
        return psutil.virtual_memory().available
    except Exception:
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                text=True,
            )
            return int(out.strip()) * 1024
        except Exception:
            return -1


def _powershell_cim(query: str) -> Optional[str]:
    """Best-effort single-value CIM/WMI query via PowerShell. Returns None
    (never raises) if PowerShell or the query is unavailable -- callers must
    treat None as 'unqueryable', not as a failure of the thing being
    queried."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", query],
            text=True, stderr=subprocess.DEVNULL, timeout=20,
        )
        val = out.strip()
        return val or None
    except Exception:
        return None


def check_model(report: ReadinessReport, hw: dict) -> Optional[Path]:
    identity = hw["fixed_identity_known_at_phase0"]["model_identity"]
    model_path = Path(identity["model_path_windows"])
    exists = model_path.is_file()
    report.add("model_path_exists", PASS if exists else BLOCKING_FAILURE, str(model_path))
    if not exists:
        report.add("model_sha256_matches", BLOCKING_FAILURE, "cannot check hash: file missing")
        return None
    actual = sha256_file(model_path)
    expected = identity["sha256"]
    report.add(
        "model_sha256_matches",
        PASS if actual == expected else BLOCKING_FAILURE,
        f"expected={expected} actual={actual}",
    )
    return model_path


def check_runtime(report: ReadinessReport, hw: dict) -> Optional[Path]:
    identity = hw["fixed_identity_known_at_phase0"]["runtime_identity"]
    expected_sha256 = identity["sha256"]

    llama_dir = os.environ.get("ARGINE_LLAMA_DIR")
    report.add("ARGINE_LLAMA_DIR_set", PASS if llama_dir else ADVISORY,
               llama_dir or "unset (runtime resolver may still resolve via another tier)")

    try:
        from argine.research2 import runtime_resolver
        resolution = runtime_resolver.resolve()
    except Exception as e:  # noqa: BLE001
        report.add("runtime_resolved", BLOCKING_FAILURE,
                   f"argine.research2.runtime_resolver.resolve() raised: {type(e).__name__}: {e}")
        report.add("runtime_sha256_matches", BLOCKING_FAILURE, "cannot check: resolution failed")
        return None

    if resolution.status != "RUNTIME_RESOLVED":
        report.add("runtime_resolved", BLOCKING_FAILURE,
                   f"status={resolution.status} message={resolution.message}")
        report.add("runtime_sha256_matches", BLOCKING_FAILURE, "cannot check: not resolved")
        return None

    runtime_path = Path(resolution.resolved_path)
    report.add("runtime_resolved", PASS,
               f"tier={resolution.tier} path={runtime_path} stale_env_note={resolution.stale_env_note}")

    actual = sha256_file(runtime_path)
    report.add(
        "runtime_sha256_matches",
        PASS if actual == expected_sha256 else BLOCKING_FAILURE,
        f"expected={expected_sha256} actual={actual}",
    )
    return runtime_path if actual == expected_sha256 else None


def check_argine_importable(report: ReadinessReport) -> bool:
    try:
        import argine  # noqa: F401
        report.add("argine_importable", PASS, getattr(argine, "__file__", "?"))
        return True
    except Exception as e:  # noqa: BLE001
        report.add("argine_importable", BLOCKING_FAILURE, f"{type(e).__name__}: {e}")
        return False


def check_python(report: ReadinessReport) -> None:
    report.add("python_interpreter", ADVISORY,
               f"{sys.executable} ({platform.python_version()})")


def check_cpu_identity(report: ReadinessReport) -> None:
    name = _powershell_cim("(Get-CimInstance Win32_Processor).Name")
    if name:
        report.add("cpu_identity_collected", PASS, name)
    else:
        report.add("cpu_identity_collected", ADVISORY,
                   f"CIM query unavailable; platform.processor()={platform.processor()!r}")


def check_physical_ram(report: ReadinessReport) -> Optional[int]:
    total = _powershell_cim(
        "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
    if total:
        try:
            total_bytes = int(total)
            report.add("physical_ram_collected", PASS, f"{total_bytes} bytes")
            return total_bytes
        except ValueError:
            pass
    report.add("physical_ram_collected", ADVISORY, "CIM query unavailable or non-numeric")
    return None


def check_gpu_identity(report: ReadinessReport) -> None:
    name = _powershell_cim(
        "(Get-CimInstance Win32_VideoController | Select-Object -First 1 -ExpandProperty Name)")
    if name:
        report.add("gpu_or_igpu_identity_collected", PASS, name)
    else:
        report.add("gpu_or_igpu_identity_collected", ADVISORY,
                   "CIM query unavailable (no frozen expected value exists yet -- "
                   "this is the first-run field that seeds HARDWARE-IDENTITY-STAMP.json)")


def check_windows_version(report: ReadinessReport) -> None:
    ver = _powershell_cim("(Get-CimInstance Win32_OperatingSystem).Caption + ' ' + "
                          "(Get-CimInstance Win32_OperatingSystem).Version")
    if ver:
        report.add("windows_version_collected", PASS, ver)
    else:
        report.add("windows_version_collected", ADVISORY,
                   f"CIM query unavailable; platform.platform()={platform.platform()!r}")


# Standard Windows power-scheme GUIDs (locale-INDEPENDENT; powercfg's
# descriptive text around the GUID is localized -- e.g. Italian output
# always includes the generic label "GUID combinazione risparmio energia"
# (literally "power-saving combination GUID") even when the ACTIVE scheme
# is Balanced, because that phrase is part of the fixed label `powercfg`
# prints before every GUID, not a description of which scheme is active.
# A naive substring match on "power saver"/"risparmio" therefore
# misclassifies Balanced (and would misclassify High Performance too,
# under other locales) as a forbidden power-saving plan. Classification
# here is by GUID only; the surrounding text is never matched again.
POWER_SCHEME_GUIDS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Power Saver",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High Performance",
}
POWER_SAVER_GUID = "a1841308-3541-4fab-bc81-f71556f20b4a"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def check_power_mode(report: ReadinessReport, machine_state: dict) -> None:
    """Classifies the active Windows power scheme by its GUID, never by the
    localized descriptive text powercfg prints around it. machine_state_
    contract.json's only requirement is that the scheme must never be
    Power Saver (never 'Power saver' for publication runs, since that mode
    deliberately throttles); it does not forbid Balanced and does not
    require High Performance, so both of those PASS identically here --
    this function invents no additional requirement for either. An
    unrecognized/custom GUID is reported ADVISORY, never guessed into
    PASS or BLOCKING_FAILURE."""
    active = _powershell_cim(
        "(powercfg /GETACTIVESCHEME)")
    if not active:
        report.add("power_mode", ADVISORY, "power scheme unqueryable on this host")
        return

    match = _GUID_RE.search(active)
    if not match:
        report.add("power_mode", ADVISORY,
                   f"could not parse a scheme GUID out of powercfg output "
                   f"(locale-independent GUID match failed); raw={active!r}")
        return

    guid = match.group(0).lower()
    scheme_name = POWER_SCHEME_GUIDS.get(guid)

    if scheme_name is None:
        report.add("power_mode", ADVISORY,
                   f"unrecognized/custom power-scheme GUID {guid} (raw={active!r}); "
                   f"machine_state_contract.json names only Power Saver as forbidden, "
                   f"so a custom scheme is neither confirmed-forbidden nor "
                   f"confirmed-safe -- not guessed either way")
        return

    if guid == POWER_SAVER_GUID:
        report.add("power_mode", BLOCKING_FAILURE,
                   f"active scheme is Power Saver ({guid}), forbidden for publication "
                   f"runs by machine_state_contract.json: raw={active!r}")
    else:
        report.add("power_mode", PASS,
                   f"active scheme is {scheme_name} ({guid}) -- not Power Saver; "
                   f"must stay fixed for the whole campaign, recorded at first real "
                   f"run; raw={active!r}")


def check_free_ram(report: ReadinessReport, model_path: Optional[Path]) -> None:
    """Reuses argine.planner.plan() feasibility -- the exact same
    authoritative, fail-closed memory gate `argine research trace` uses --
    never a model-file-size heuristic. Falls back to the fixed 8 GiB
    publication-run threshold already established for this hardware class
    (tools/r2_11_capture_driver.py) only if the real planner cannot be
    invoked here; this is a REUSED tolerance, not a newly invented one."""
    FALLBACK_MIN_FREE_RAM_BYTES = 8 * 1024 ** 3
    free_ram = free_ram_bytes()

    if model_path is None:
        report.add("sufficient_free_ram", BLOCKING_FAILURE,
                   "cannot plan feasibility: model path did not resolve")
        return

    try:
        from argine import hardware as hwmod
        from argine import planner as planmod
        from argine.gguf import find_gguf, read_gguf
        from argine.util import parse_size

        # Frozen primary (4 GiB) budget is the authoritative feasibility
        # target for the readiness gate; secondary budgets are <= primary
        # and 1.5 GiB is smaller still, so 4 GiB is the binding constraint.
        gguf_path = find_gguf(str(model_path))
        model = read_gguf(gguf_path)
        hw = hwmod.detect(extra_disk_paths=[str(model_path.parent)])
        req = planmod.PlanRequest(
            ctx=4096, threads=4, backend="cpu",
            expert_store=True, expert_store_budget=parse_size("4G"),
        )
        plan = planmod.plan(model, hw, req)
        report.add(
            "sufficient_free_ram",
            PASS if plan.feasible else BLOCKING_FAILURE,
            f"argine.planner.plan() feasible={plan.feasible} "
            f"estimate_total={plan.estimate.total} failure_reason={plan.failure_reason} "
            f"free_ram={free_ram}",
        )
    except Exception as e:  # noqa: BLE001
        report.add(
            "sufficient_free_ram",
            PASS if (free_ram >= 0 and free_ram >= FALLBACK_MIN_FREE_RAM_BYTES) else BLOCKING_FAILURE,
            f"[FALLBACK -- planner unavailable: {type(e).__name__}: {e}] "
            f"free={free_ram} required>={FALLBACK_MIN_FREE_RAM_BYTES} "
            f"(reused fixed 8 GiB threshold, not newly invented)",
        )


def check_expert_store(report: ReadinessReport, model_path: Optional[Path]) -> None:
    if model_path is None:
        report.add("expert_store_path_exists", BLOCKING_FAILURE,
                   "cannot locate expert store: model path did not resolve")
        report.add("storage_device_hosting_expert_store_collected", ADVISORY,
                   "skipped: model path did not resolve")
        return
    store_path = model_path.with_suffix(".psxs")
    exists = store_path.is_file()
    report.add(
        "expert_store_path_exists",
        PASS if exists else BLOCKING_FAILURE,
        f"{store_path} -- required for R2-12 (without a built store the "
        f"PredictedProtectPolicy-vs-B_selected comparison degrades to plain "
        f"mmap and is not the comparison this protocol measures; build it "
        f"with `argine store build`)",
    )
    drive = _powershell_cim(
        f"(Get-Partition -DriveLetter ('{store_path.drive}'.TrimEnd(':'))).DiskNumber"
    ) if store_path.drive else None
    if drive:
        report.add("storage_device_hosting_expert_store_collected", PASS,
                   f"disk number {drive} for {store_path.drive}")
    else:
        report.add("storage_device_hosting_expert_store_collected", ADVISORY,
                   "disk-number query unavailable (no frozen expected value exists "
                   "yet -- this is a first-run stamp field)")


def check_no_concurrent_writer(report: ReadinessReport) -> None:
    report.add("no_concurrent_readiness_writer", PASS if not READINESS_LOCK_PATH.exists()
               else BLOCKING_FAILURE, str(READINESS_LOCK_PATH))
    report.add("no_concurrent_execution_campaign", PASS if not EXECUTION_LOCK_PATH.exists()
               else BLOCKING_FAILURE, str(EXECUTION_LOCK_PATH))

    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue).Count"],
            text=True, stderr=subprocess.DEVNULL, timeout=20,
        )
        n = int(out.strip() or "0")
        report.add("no_concurrent_llama_server_process", PASS if n == 0 else BLOCKING_FAILURE,
                   f"{n} llama-server.exe process(es) already running")
    except Exception as e:  # noqa: BLE001
        report.add("no_concurrent_llama_server_process", ADVISORY,
                   f"process query unavailable: {type(e).__name__}: {e}")


def check_output_root(report: ReadinessReport) -> None:
    results_root = CONTRACT_DIR / "runtime_results"
    if not results_root.exists():
        report.add("runtime_output_root_available", PASS,
                   f"{results_root} does not exist yet; will be created by the "
                   f"execution driver on first write")
        return
    if not results_root.is_dir():
        report.add("runtime_output_root_available", BLOCKING_FAILURE,
                   f"{results_root} exists and is not a directory")
        return
    try:
        testfile = results_root / f".readiness_write_probe_{os.getpid()}.tmp"
        testfile.write_text("probe", encoding="utf-8")
        testfile.unlink()
        report.add("runtime_output_root_available", PASS, f"{results_root} exists and is writable")
    except Exception as e:  # noqa: BLE001
        report.add("runtime_output_root_available", BLOCKING_FAILURE,
                   f"{results_root} exists but is not writable: {type(e).__name__}: {e}")


def check_instrumentation_and_equivalence_capability(
    report: ReadinessReport, instrumentation: dict, output_equivalence: dict
) -> None:
    fields = instrumentation.get("fields_recorded_per_run", [])
    report.add("instrumentation_fields_contract_loadable",
               PASS if fields else BLOCKING_FAILURE,
               f"{len(fields)} fields: {fields}")

    try:
        import hashlib
        hashlib.sha256(b"probe")
        report.add("output_equivalence_hashing_capability", PASS, "hashlib.sha256 available")
    except Exception as e:  # noqa: BLE001
        report.add("output_equivalence_hashing_capability", BLOCKING_FAILURE,
                   f"{type(e).__name__}: {e}")

    try:
        from argine.research2.r2_01 import trace_contract  # noqa: F401
        report.add("trace_comparison_machinery_importable", PASS,
                   "argine.research2.r2_01.trace_contract importable "
                   "(reused for separate scientific-behavior verification)")
    except Exception as e:  # noqa: BLE001
        report.add("trace_comparison_machinery_importable", ADVISORY,
                   f"{type(e).__name__}: {e}")


def check_operator_attestation(report: ReadinessReport, attested: bool) -> None:
    report.add(
        "operator_attested_clean_environment",
        PASS if attested else BLOCKING_FAILURE,
        "set via --operator-attests-clean-environment; covers the conditions "
        "machine_state_contract.json marks as not machine-checkable (no "
        "YouTube/streaming video, Chrome fully closed, no heavy background "
        "workloads, no Windows Update in progress) -- never defaulted to True",
    )


def run_readiness_checks(*, operator_attests_clean_environment: bool) -> ReadinessReport:
    report = ReadinessReport()

    hw = load_json(CONTRACT_DIR / "hardware_contract.json")
    machine_state = load_json(CONTRACT_DIR / "machine_state_contract.json")
    instrumentation = load_json(CONTRACT_DIR / "instrumentation_contract.json")
    output_equivalence = load_json(CONTRACT_DIR / "output_equivalence.json")

    check_python(report)
    argine_ok = check_argine_importable(report)
    model_path = check_model(report, hw)
    check_runtime(report, hw)
    check_cpu_identity(report)
    check_physical_ram(report)
    check_gpu_identity(report)
    check_windows_version(report)
    check_power_mode(report, machine_state)
    if argine_ok:
        check_free_ram(report, model_path)
    else:
        report.add("sufficient_free_ram", BLOCKING_FAILURE, "skipped: argine not importable")
    check_expert_store(report, model_path)
    check_no_concurrent_writer(report)
    check_output_root(report)
    check_instrumentation_and_equivalence_capability(report, instrumentation, output_equivalence)
    check_operator_attestation(report, operator_attests_clean_environment)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operator-attests-clean-environment", action="store_true", default=False,
        help="Required to pass the operator_attested_clean_environment check. "
             "Only pass this if you have personally verified: no YouTube/streaming "
             "video anywhere, no browser video playback, no heavy background "
             "workloads, no Windows Update in progress, Chrome fully closed.",
    )
    parser.add_argument("--out", default=str(REPORT_PATH), help="Report output path.")
    args = parser.parse_args()

    report = run_readiness_checks(
        operator_attests_clean_environment=args.operator_attests_clean_environment
    )

    out_obj = report.to_json()
    out_obj["generated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_obj["note"] = (
        "This report alone does NOT constitute R2_12_RUNTIME_READY. That state "
        "transition is a separate, explicit commit made only after this driver "
        "has been run for real on the target host with overall=PASS and the "
        "report has been reviewed."
    )

    out_path = Path(args.out)
    out_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")

    print(json.dumps(out_obj, indent=2))
    print()
    print(f"OVERALL: {report.overall}  "
          f"(pass={out_obj['n_pass']} blocking_failure={out_obj['n_blocking_failure']} "
          f"advisory={out_obj['n_advisory']})")
    print(f"Report written to: {out_path}")

    return 0 if report.overall == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
