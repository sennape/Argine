"""Synthetic fixtures for the Phase 2 tests: argine-raw traces per arm and complete launch-record sets.
No evaluation prompt text exists here or is ever read: prompts are opaque ids and fake strings."""
from __future__ import annotations

import copy, importlib.util, json, random, sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
MS = 1_000_000
NT = 12
MODE = {"layer": 0, "layer_touch": 1, "kind": 2}


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod)   # type: ignore
    return mod


class Trace:
    def __init__(self, wait_mode: str = "kind", schema: str = "r5-ets-v1", tid_base: int = 0, mode: Optional[int] = None):
        self.lines = [f"C\texpert_wait\t{wait_mode}", f"C\tets_schema\t{schema}"]
        self.req, self.tid_base = 0, tid_base
        self.mode = MODE[wait_mode] if mode is None else mode

    def graph(self, g: int, n_tokens: int = 1) -> None:
        self.lines.append(f"R\t{g}\t0\t0\t{n_tokens}\t8")

    def node(self, g, layer, kind, first, t_in, t_out, waits: Dict[int, Tuple[int, int]], nowait: bool = False) -> None:
        for i in range(NT):
            ws, we = waits.get(i, (0, 0) if nowait else (t_in + 1, t_in + 2))
            self.lines.append("\t".join(map(str, ["T", g, layer, kind, self.tid_base + i, self.mode, first, t_in, ws, we, t_out, 7, 0, 0, 0, 0, 0])))

    def read(self, layer, kind, issue, complete, flags=1, tid=None, nbytes=4096, ok=1) -> None:
        self.req += 1
        tid = self.tid_base + (self.req % NT if tid is None else tid)
        self.lines.append("\t".join(map(str, ["I", self.req, tid, layer, 3, kind, nbytes, issue, complete, 0, 0, ok, flags])))

    def event(self, g: int, t0: int, waits: Tuple[int, int, int] = (500, 100, 100), deferred_complete: Tuple[int, int] = (300, 300),
              flags: Tuple[int, int] = (1, 1), nbytes: Tuple[int, int] = (4096, 4096), gap: int = 2000, node_len: int = 1000,
              nowait_later: bool = False) -> None:
        """A 3-node layer event: first node (kind 0) then kinds 1 and 2. waits = critical waits (thread 0) of the 3 nodes,
        each starting 10 ns after the node's entry. Reads issued at the first node: one of kind 0 (consumed there) and
        one deferred read of each of kinds 1 and 2, completing `deferred_complete` ns after t0."""
        t = t0
        starts = []
        for k in range(3):
            starts.append(t)
            w = {0: (t + 10, t + 10 + waits[k])} if not (k and nowait_later) else {}
            self.node(g, 0, k, 1 if k == 0 else 0, t, t + node_len, w, nowait=bool(k and nowait_later))
            t += node_len + gap
        self.read(0, 0, t0 + 5, t0 + 200)
        for j, k in enumerate((1, 2)):
            self.read(0, k, t0 + 5, t0 + deferred_complete[j], flags=flags[j], nbytes=nbytes[j])
        self._starts = starts


def launch_trace(wait_mode: str, n_decode: int = 511, **ev) -> str:
    t = Trace(wait_mode)
    t.graph(0, n_tokens=40)
    t.event(0, 1000)
    for g in range(1, n_decode + 1):
        t.graph(g)
        t.event(g, g * 10 * MS, **ev)
    return "\n".join(t.lines) + "\n"


# ------------------------------------------------------------------------------------------------ launch records
def g1_templates() -> Dict[str, dict]:
    log = REPO / "experiments" / "R5-01-g1-host-evidence" / "host" / "DEV_LOG.jsonl"
    r = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {"A": r[0], "C0_I": r[1], "C": r[2], "B": r[40], "C_I": r[41]}


DIGEST = {p: f"digest-{p}" for p in ("code-eval-1", "nl-eval-1", "math-eval-1", "json-eval-1")}


def base_record(slot: dict, tmpl: Dict[str, dict], ms_per_token: float, budget_bytes: Optional[int] = None) -> dict:
    t = copy.deepcopy(tmpl[slot["arm"]])
    t.update({k: slot[k] for k in ("seq", "campaign", "block", "pos", "arm", "prompt", "budget", "traced", "counted")})
    t["type"], t["key"] = "launch", (str(slot["seq"]) if slot["counted"] else f"warmup:{slot['campaign']}")
    t["attempt"], t["problems"] = 1, []
    t["server_timings"] = dict(t["server_timings"], predicted_ms=ms_per_token * 512, predicted_n=512)
    t["token_digest"] = DIGEST[slot["prompt"]]
    t["r5_counters"] = dict(t["r5_counters"], budget=budget_bytes or {"1.5": 1610612736, "4": 4294967296}[slot["budget"]])
    return t


def record_set(effect_15: float = 0.05, effect_4: float = 0.0, noise: float = 0.002, seed: int = 7, prompt_effect: Optional[Dict[str, float]] = None,
               mech: Optional[Dict[str, Tuple[float, float, float]]] = None, hidden: float = 0.6) -> Tuple[List[dict], Dict[int, dict]]:
    """196 counted records following the frozen schedule, and mechanism metrics for the 36 traced launches.
    mech: arm -> (exposed_wait_ms, exposed_store_ms, deferred bytes per token)."""
    C = load("campaign", HERE / "campaign.py")
    rnd = random.Random(seed)
    tmpl = g1_templates()
    mech = mech or {"B": (80.0, 190.0, 4e8), "C0_I": (80.0, 185.0, 4e8), "C_I": (60.0, 165.0, 4e8)}
    recs, mm = [], {}
    for s in C.plan():
        base = 280.0 if s["budget"] == "1.5" else 150.0
        if s["arm"] == "C":
            eff = (prompt_effect or {}).get(s["prompt"], effect_15) if s["budget"] == "1.5" else effect_4
            v = base * (1 - eff)
        else:
            v = base
        recs.append(base_record(s, tmpl, v * (1 + rnd.uniform(-noise, noise))))
        if s["traced"]:
            w, st, d = mech[s["arm"]]
            mm[s["seq"]] = {"exposed_wait_ms_per_token": w * (1 + rnd.uniform(-0.01, 0.01)), "exposed_store_ms_per_token": st,
                            "deferred_read_bytes_per_token": d, "hidden_deferred_bytes_fraction": hidden if s["arm"] == "C_I" else 1.0}
    return recs, mm
