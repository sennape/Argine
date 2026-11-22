"""R3-02G multi-session execution: immutable global schedule + resumable sessions.

TIMING-only, extension-only, 600 fresh A/B pairs at 4 GiB in balanced ABBA/BAAB
blocks. This module builds the schedule, enforces session ranges, resumes from
persisted state, and exposes an operational-only monitor.

It never computes scientific aggregates (no medians, CIs or precision verdicts).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import r3_02_acquire as A  # noqa: E402

PHASE = REPO / "experiments" / "R3-02H-multisession"
DEFAULT_PLAN = REPO / "experiments" / "R3-02-calibration-mechanism" / "ACQUISITION_PLAN.json"

SESSIONS = 10
PAIRS_PER_SESSION = 60
BLOCKS_PER_SESSION = 30
PAIRS_TOTAL = SESSIONS * PAIRS_PER_SESSION          # 600
BLOCKS_TOTAL = PAIRS_TOTAL // 2                      # 300
LAUNCHES_TOTAL = PAIRS_TOTAL * 2                     # 1200
BLOCK_ORIENTATIONS = ("ABBA", "BAAB")
WARMUPS = [{"warmup": 1, "policy": "A", "config_id": "A-4GiB", "n_predict": 512, "counted": False},
           {"warmup": 2, "policy": "B", "config_id": "B-4GiB", "n_predict": 512, "counted": False}]

# ---------------------------------------------------------------------------
# Execution adapter: R3-02H schedule entry -> R3-02B execution launch
# ---------------------------------------------------------------------------

EXECUTION_KIND = "TIMING"          # R3-02G is TIMING-only
BUDGET_BYTES = 4 * 1024 ** 3       # frozen 4 GiB configuration
POLICY_IDS = {"A": "b_selected", "B": "predicted_protect_sham"}
WARMUP_SEQ = 0                      # uncounted; log names 00-A-4GiB / 00-B-4GiB



def build_schedule() -> Dict[str, Any]:
    launches: List[Dict[str, Any]] = []
    pairs: List[Dict[str, Any]] = []
    launch_seq = 0
    for block in range(1, BLOCKS_TOTAL + 1):
        orientation = BLOCK_ORIENTATIONS[(block - 1) % 2]
        for offset in range(2):                       # two pairs per block
            pair_no = (block - 1) * 2 + offset + 1
            session = (pair_no - 1) // PAIRS_PER_SESSION + 1
            session_pair = (pair_no - 1) % PAIRS_PER_SESSION + 1
            if orientation == "ABBA":
                pair_orientation = "AB" if offset == 0 else "BA"
            else:
                pair_orientation = "BA" if offset == 0 else "AB"
            order = ("A", "B") if pair_orientation == "AB" else ("B", "A")
            pair_launches = []
            for slot, policy in enumerate(order, start=1):
                launch_seq += 1
                launch = {
                    "global_launch": launch_seq, "pair": pair_no, "session": session,
                    "session_pair": session_pair, "block": block,
                    "block_orientation": orientation, "orientation": pair_orientation,
                    "slot": slot, "policy": policy,
                    "config_id": f"{policy}-4GiB", "n_predict": 512, "counted": True,
                }
                launches.append(launch)
                pair_launches.append(launch_seq)
            pairs.append({"pair": pair_no, "session": session, "session_pair": session_pair,
                          "block": block, "block_orientation": orientation,
                          "orientation": pair_orientation, "launches": pair_launches})
    return {"launches": launches, "pairs": pairs, "warmups": WARMUPS}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def schedule_digest(schedule: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(schedule).encode("utf-8")).hexdigest()


def validate_schedule(schedule: Optional[Dict[str, Any]] = None) -> List[str]:
    s = schedule or build_schedule()
    f: List[str] = []
    pairs = [p["pair"] for p in s["pairs"]]
    if pairs != list(range(1, PAIRS_TOTAL + 1)):
        f.append("pair numbering not 1..600")
    seqs = [l["global_launch"] for l in s["launches"]]
    if seqs != list(range(1, LAUNCHES_TOTAL + 1)):
        f.append("launch numbering not 1..1200")
    if len({(l["pair"], l["slot"]) for l in s["launches"]}) != LAUNCHES_TOTAL:
        f.append("duplicate (pair, slot) launch identity")
    for session in range(1, SESSIONS + 1):
        ls = [l for l in s["launches"] if l["session"] == session]
        if len(ls) != PAIRS_PER_SESSION * 2:
            f.append(f"session {session}: {len(ls)} launches != 120")
        sp = sorted({l["session_pair"] for l in ls})
        if sp != list(range(1, PAIRS_PER_SESSION + 1)):
            f.append(f"session {session}: session_pair range wrong")
        blocks = sorted({l["block"] for l in ls})
        if len(blocks) != BLOCKS_PER_SESSION:
            f.append(f"session {session}: {len(blocks)} blocks != 30")
        if blocks[0] != (session - 1) * BLOCKS_PER_SESSION + 1:
            f.append(f"session {session}: block start misaligned")
    # pair never crosses a session boundary
    for p in s["pairs"]:
        sessions = {l["session"] for l in s["launches"] if l["pair"] == p["pair"]}
        if len(sessions) != 1:
            f.append(f"pair {p['pair']} crosses sessions")
    ab = sum(1 for p in s["pairs"] if p["orientation"] == "AB")
    ba = sum(1 for p in s["pairs"] if p["orientation"] == "BA")
    if (ab, ba) != (PAIRS_TOTAL // 2, PAIRS_TOTAL // 2):
        f.append(f"AB/BA imbalance: {ab}/{ba}")
    for block in range(1, BLOCKS_TOTAL + 1):
        orient = BLOCK_ORIENTATIONS[(block - 1) % 2]
        ps = [p["orientation"] for p in s["pairs"] if p["block"] == block]
        expect = ["AB", "BA"] if orient == "ABBA" else ["BA", "AB"]
        if ps != expect:
            f.append(f"block {block} orientation sequence wrong")
    return f


def _plan_config(plan: Dict[str, Any], config_id: str) -> Dict[str, Any]:
    try:
        cfg = next(c for c in plan["configurations"] if c["config_id"] == config_id)
    except StopIteration:
        raise ValueError(f"unknown configuration {config_id!r}") from None
    if int(cfg["budget_bytes"]) != BUDGET_BYTES:
        raise ValueError(f"{config_id}: budget is not the frozen 4 GiB")
    return cfg


def build_execution_launch(entry: Dict[str, Any], plan: Dict[str, Any], *,
                           warmup: bool = False) -> Dict[str, Any]:
    """Convert one frozen R3-02H schedule entry into the R3-02B execution schema.

    Returns a new transient dict; the canonical schedule entry is never mutated.
    Fails closed on any identity or ordering inconsistency.
    """
    policy = entry.get("policy")
    if policy not in POLICY_IDS:
        raise ValueError(f"invalid policy {policy!r}")
    config_id = entry.get("config_id")
    if config_id != f"{policy}-4GiB":
        raise ValueError(f"config {config_id!r} does not match policy {policy!r}")
    cfg = _plan_config(plan, config_id)
    if cfg["policy_id"] != POLICY_IDS[policy]:
        raise ValueError(f"{config_id}: policy id mismatch with frozen plan")
    if int(entry.get("n_predict") or 0) != 512:
        raise ValueError("n_predict is not the frozen 512")

    if warmup:
        if not entry.get("warmup") in (1, 2):
            raise ValueError("warmup index must be 1 or 2")
        return {
            "seq": WARMUP_SEQ, "kind": EXECUTION_KIND, "config_id": config_id,
            "n_predict": 512, "repetition": None, "counted": False, "warmup": True,
            "warmup_index": entry["warmup"], "pair": None, "session": 1,
            "session_pair": None, "block": None, "policy": policy,
            "orientation": None, "global_launch": None,
        }

    seq = entry.get("global_launch")
    pair = entry.get("pair")
    session = entry.get("session")
    session_pair = entry.get("session_pair")
    if not isinstance(seq, int) or not 1 <= seq <= LAUNCHES_TOTAL:
        raise ValueError(f"invalid global launch {seq!r}")
    if not isinstance(pair, int) or not 1 <= pair <= PAIRS_TOTAL:
        raise ValueError(f"invalid pair {pair!r}")
    if session != (pair - 1) // PAIRS_PER_SESSION + 1:
        raise ValueError("session does not match pair range")
    if session_pair != (pair - 1) % PAIRS_PER_SESSION + 1:
        raise ValueError("session_pair does not match global pair")
    if entry.get("slot") not in (1, 2):
        raise ValueError("slot must be 1 or 2")
    expected_slot = 1 if entry.get("orientation") == "AB" else 2
    if policy == "A" and entry.get("slot") != expected_slot:
        raise ValueError("A launch slot inconsistent with pair orientation")
    if policy == "B" and entry.get("slot") == expected_slot:
        raise ValueError("B launch slot inconsistent with pair orientation")
    if entry.get("counted") is not True:
        raise ValueError("counted launch is not marked counted")

    return {
        "seq": seq, "kind": EXECUTION_KIND, "config_id": config_id,
        "n_predict": 512, "repetition": pair, "counted": True, "warmup": False,
        "pair": pair, "session": session, "session_pair": session_pair,
        "block": entry.get("block"), "policy": policy,
        "orientation": entry.get("orientation"), "global_launch": seq,
        "block_orientation": entry.get("block_orientation"),
    }


def execution_launches(schedule: Dict[str, Any], plan: Dict[str, Any], session: int) -> List[Dict[str, Any]]:
    """Warmups (session 1 only) followed by the session's adapted counted launches."""
    out: List[Dict[str, Any]] = []
    if session == 1:
        for w in schedule["warmups"]:
            out.append(build_execution_launch(w, plan, warmup=True))
    for entry in session_launches(schedule, session):
        out.append(build_execution_launch(entry, plan))
    return out


class SessionState:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.data: Dict[str, Any] = {"launches": {}, "sessions": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def status(self, global_launch: int) -> str:
        return (self.data["launches"].get(str(global_launch)) or {}).get("status", "PLANNED")

    def mark(self, global_launch: int, status: str, **extra: Any) -> None:
        rec = dict(self.data["launches"].get(str(global_launch)) or {})
        rec.update({"status": status, **extra})
        self.data["launches"][str(global_launch)] = rec
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def session_launches(schedule: Dict[str, Any], session: int) -> List[Dict[str, Any]]:
    if not 1 <= session <= SESSIONS:
        raise ValueError(f"session must be 1..{SESSIONS}")
    return [l for l in schedule["launches"] if l["session"] == session]


def pairs_complete(schedule: Dict[str, Any], state: SessionState, session: Optional[int] = None) -> int:
    pairs = schedule["pairs"] if session is None else [p for p in schedule["pairs"] if p["session"] == session]
    done = 0
    for p in pairs:
        if all(state.status(seq) == "SUCCEEDED" for seq in p["launches"]):
            done += 1
    return done


def launches_complete(state: SessionState, session: Optional[int] = None,
                      schedule: Optional[Dict[str, Any]] = None) -> int:
    if session is None:
        return sum(1 for v in state.data["launches"].values() if v.get("status") == "SUCCEEDED")
    s = schedule or build_schedule()
    return sum(1 for l in s["launches"] if l["session"] == session and state.status(l["global_launch"]) == "SUCCEEDED")


def failures(state: SessionState, session: Optional[int] = None, schedule: Optional[Dict[str, Any]] = None) -> int:
    if session is None:
        return sum(1 for v in state.data["launches"].values() if v.get("status") == "FAILED")
    s = schedule or build_schedule()
    return sum(1 for l in s["launches"] if l["session"] == session and state.status(l["global_launch"]) == "FAILED")


def analysis_status(state: SessionState, schedule: Dict[str, Any], out_dir: pathlib.Path) -> Dict[str, Any]:
    pairs = pairs_complete(schedule, state)
    launches = launches_complete(state)
    admissions = len(list((Path(out_dir) / "admissions").glob("timing-*.json")))
    ready = pairs == PAIRS_TOTAL and launches == LAUNCHES_TOTAL and admissions >= LAUNCHES_TOTAL
    return {"status": "R3_02G_EVAL_READY" if ready else "R3_02G_EVAL_LOCKED",
            "pairs_complete": pairs, "launches_complete": launches,
            "admissions": admissions, "required_pairs": PAIRS_TOTAL,
            "required_launches": LAUNCHES_TOTAL}


def monitor(state: SessionState, schedule: Dict[str, Any], out_dir: pathlib.Path,
            session: Optional[int] = None) -> Dict[str, Any]:
    import psutil
    usage = psutil.virtual_memory()
    info = {
        "session": session,
        "sessions_total": SESSIONS,
        "session_pairs_complete": pairs_complete(schedule, state, session) if session else None,
        "session_pairs_required": PAIRS_PER_SESSION if session else None,
        "global_pairs_complete": pairs_complete(schedule, state),
        "global_pairs_required": PAIRS_TOTAL,
        "counted_launches_complete": launches_complete(state),
        "counted_launches_required": LAUNCHES_TOTAL,
        "failures": failures(state),
        "ram_available_gib": round(usage.available / 1024 ** 3, 2),
        "disk_free_gib": round(A.free_bytes(Path(out_dir)) / 1024 ** 3, 2),
        "process_status": "IDLE",
    }
    return info


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="R3-02G multi-session execution")
    ap.add_argument("--session", type=int, choices=range(1, SESSIONS + 1))
    ap.add_argument("--out-dir", default=r"C:\Users\alpel\Desktop\Argine4-r3runs\R3-02G")
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--print-schedule", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--monitor", action="store_true")
    ap.add_argument("--analysis-status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--attest-clean-environment", action="store_true")
    args = ap.parse_args(argv)

    schedule = build_schedule()
    failures_list = validate_schedule(schedule)
    if failures_list:
        print("SCHEDULE INVALID:", json.dumps(failures_list, indent=1))
        return 2
    if args.print_schedule:
        print(json.dumps({"sessions": SESSIONS, "pairs": PAIRS_TOTAL,
                          "launches": LAUNCHES_TOTAL, "digest": schedule_digest(schedule),
                          "warmups": schedule["warmups"]}, indent=2, sort_keys=True))
        return 0
    state = SessionState(Path(args.out_dir) / "state.json")
    if args.verify:
        print(json.dumps({"digest": schedule_digest(schedule), "failures": validate_schedule(schedule)},
                         indent=2, sort_keys=True))
        return 0
    if args.monitor:
        print(json.dumps(monitor(state, schedule, Path(args.out_dir), args.session), indent=2, sort_keys=True))
        return 0
    if args.analysis_status:
        print(json.dumps(analysis_status(state, schedule, Path(args.out_dir)), indent=2, sort_keys=True))
        return 0
    if not args.session:
        print("session required for execution"); return 2
    launches = session_launches(schedule, args.session)
    if args.dry_run:
        plan_doc = A.load_json(Path(args.plan))
        adapted = execution_launches(schedule, plan_doc, args.session)
        counted = [l for l in adapted if l["counted"]]
        warmups = [l for l in adapted if l["warmup"]]
        print(json.dumps({"session": args.session, "counted_launches": len(counted),
                          "warmups": len(warmups), "pairs": PAIRS_PER_SESSION,
                          "blocks": BLOCKS_PER_SESSION,
                          "kinds": sorted({l["kind"] for l in adapted}),
                          "already_done": sum(1 for l in launches
                                              if state.status(l["global_launch"]) == "SUCCEEDED")},
                         indent=2, sort_keys=True))
        return 0
    if not args.attest_clean_environment:
        print("REFUSING TO LAUNCH: --attest-clean-environment is required"); return 3
    plan = A.load_json(Path(args.plan))
    runner = A.SubprocessRunner(plan, Path(args.out_dir))
    succeeded = 0
    adapted = execution_launches(schedule, plan, args.session)
    for launch in adapted:
        seq = launch["seq"]
        key = f"warmup-{launch['warmup_index']}" if launch["warmup"] else seq
        if (state.data["launches"].get(str(key)) or {}).get("status") == "SUCCEEDED":
            continue
        A.default_free_ram_check(BUDGET_BYTES)
        result = A._default_run_one(launch, plan, Path(args.out_dir), runner)
        if not A.launch_clean(result.get("evidence") or {}):
            state.mark(key, "FAILED", error="clean-launch predicate failed")
        else:
            state.mark(key, "SUCCEEDED", evidence=result.get("evidence") or {})
            if launch["counted"]:
                succeeded += 1
    state.data["sessions"][str(args.session)] = {"completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                 "launches": len(launches)}
    state.save()
    print(json.dumps({"session": args.session, "completed": succeeded,
                      "pairs_complete": pairs_complete(schedule, state, args.session),
                      "failures": failures(state, args.session, schedule)}, indent=2, sort_keys=True))
    return 0 if failures(state, args.session, schedule) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
