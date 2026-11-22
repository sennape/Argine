"""The research2 experiment state machine (research2/protocol.json).

Loads the declared states + transition table and validates transitions.
Fails closed: an unrecognised state or an edge not in the table is rejected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Set, Tuple


class ProtocolError(ValueError):
    pass


class TransitionError(ValueError):
    def __init__(self, from_state: str, to_state: str, reason: str):
        super().__init__(f"illegal transition {from_state!r} -> {to_state!r}: {reason}")
        self.from_state = from_state
        self.to_state = to_state


class Protocol:
    def __init__(self, data: dict):
        self.states: List[str] = data.get("states", [])
        self.terminal_run_states: List[str] = data.get("terminal_run_states", [])
        self.transitions: Set[Tuple[str, str]] = {tuple(t) for t in data.get("transitions", [])}

    def is_valid_state(self, state: str) -> bool:
        return state in self.states

    def can_transition(self, from_state: str, to_state: str) -> bool:
        return (from_state, to_state) in self.transitions

    def validate_transition(self, from_state: str, to_state: str) -> None:
        """Raises TransitionError if the transition is not legal. Fails
        closed: unknown states and edges not in the table are both rejected."""
        if not self.is_valid_state(from_state):
            raise TransitionError(from_state, to_state, f"unknown source state {from_state!r}")
        if not self.is_valid_state(to_state):
            raise TransitionError(from_state, to_state, f"unknown target state {to_state!r}")
        if from_state == to_state:
            raise TransitionError(from_state, to_state, "no-op transitions are not permitted")
        if not self.can_transition(from_state, to_state):
            raise TransitionError(from_state, to_state, "not in the declared transition table")

    def validate(self) -> List[str]:
        problems: List[str] = []
        required = {"PLANNED", "IMPLEMENTED", "READY", "RUNNING", "COMPLETED",
                    "INCONCLUSIVE", "FAILED", "BLOCKED"}
        if set(self.states) != required:
            problems.append(f"states must be exactly {sorted(required)}, got {sorted(self.states)}")
        for (a, b) in self.transitions:
            if a not in self.states:
                problems.append(f"transition source {a!r} is not a declared state")
            if b not in self.states:
                problems.append(f"transition target {b!r} is not a declared state")
        for t in self.terminal_run_states:
            if t not in self.states:
                problems.append(f"terminal_run_state {t!r} is not a declared state")
        return problems


def load(path: Optional[str | Path] = None) -> Protocol:
    p = Path(path) if path else default_path()
    if not p.exists():
        raise ProtocolError(f"protocol not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProtocolError(f"protocol is not valid JSON ({p}): {e}") from e
    return Protocol(data)


def default_path() -> Path:
    return Path(__file__).resolve().parents[2] / "research2" / "protocol.json"
