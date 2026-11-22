"""Loading and validation of research2/roadmap.json.

The roadmap is tracked, immutable research planning/configuration: phases,
experiment IDs/names, dependencies, and declarative metadata. It contains no
scientific conclusions and no mutable run state (see store.py for that).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


class RoadmapError(ValueError):
    """Raised when research2/roadmap.json is missing, malformed, or invalid."""


class Roadmap:
    def __init__(self, data: dict, path: Path):
        self.data = data
        self.path = path
        self.phases: List[dict] = data.get("phases", [])
        self.phase_order: List[str] = data.get("phase_order", [])
        self.experiments: Dict[str, dict] = data.get("experiments", {})

    # -- accessors ---------------------------------------------------------

    def experiment_ids(self) -> List[str]:
        return list(self.experiments.keys())

    def phase_of(self, experiment_id: str) -> Optional[str]:
        exp = self.experiments.get(experiment_id)
        return exp.get("phase") if exp else None

    def depends_on(self, experiment_id: str) -> List[str]:
        exp = self.experiments.get(experiment_id)
        return list(exp.get("depends_on", [])) if exp else []

    def is_implemented(self, experiment_id: str) -> bool:
        exp = self.experiments.get(experiment_id)
        return bool(exp.get("implemented", False)) if exp else False

    def ordered_experiment_ids(self) -> List[str]:
        """Experiment IDs in canonical roadmap order: phase order, then the
        order experiments are declared within each phase."""
        order: List[str] = []
        for phase_id in self.phase_order:
            phase = next((p for p in self.phases if p.get("id") == phase_id), None)
            if not phase:
                continue
            order.extend(phase.get("experiments", []))
        # include anything not reachable via phase_order (defensive; audit() will flag it)
        for exp_id in self.experiments:
            if exp_id not in order:
                order.append(exp_id)
        return order

    # -- validation ----------------------------------------------------

    def validate(self) -> List[str]:
        """Returns a list of human-readable problems. Empty list = valid.
        Never raises; callers decide whether problems are fatal."""
        problems: List[str] = []

        phase_ids = [p.get("id") for p in self.phases]
        if len(set(phase_ids)) != len(phase_ids):
            problems.append(f"duplicate phase ids in 'phases': {phase_ids}")
        if sorted(phase_ids) != sorted(set(self.phase_order)) if self.phase_order else False:
            pass  # phase_order subset check below is stricter and clearer
        for pid in self.phase_order:
            if pid not in phase_ids:
                problems.append(f"phase_order references unknown phase id: {pid}")
        if set(self.phase_order) != set(phase_ids):
            problems.append(
                f"phase_order {self.phase_order} does not match declared phases {phase_ids}")

        expected_phase_order = ["II-A", "II-B", "II-C"]
        if self.phase_order != expected_phase_order:
            problems.append(
                f"canonical phase order must be exactly {expected_phase_order}, got {self.phase_order}")

        # experiment id uniqueness across phases' experiment lists
        seen: Dict[str, str] = {}
        for phase in self.phases:
            for exp_id in phase.get("experiments", []):
                if exp_id in seen:
                    problems.append(
                        f"experiment id {exp_id} listed in both phase {seen[exp_id]} and {phase.get('id')}")
                seen[exp_id] = phase.get("id")

        expected_ids = [f"R2-{i:02d}" for i in range(1, 13)]
        actual_ids = sorted(self.experiments.keys())
        if sorted(expected_ids) != actual_ids:
            problems.append(
                f"experiments must be exactly R2-01..R2-12 (each once); found {actual_ids}")

        valid_states = {"PLANNED", "IMPLEMENTED", "READY", "RUNNING", "COMPLETED",
                        "INCONCLUSIVE", "FAILED", "BLOCKED"}
        for exp_id, exp in self.experiments.items():
            if exp.get("phase") not in phase_ids:
                problems.append(f"{exp_id}: phase {exp.get('phase')!r} is not a declared phase")
            if exp_id not in seen:
                problems.append(f"{exp_id}: not listed under any phase's 'experiments' array")
            elif seen.get(exp_id) != exp.get("phase"):
                problems.append(
                    f"{exp_id}: declared phase {exp.get('phase')!r} does not match the phase "
                    f"that lists it ({seen.get(exp_id)!r})")
            init_state = exp.get("initial_state")
            if init_state not in valid_states:
                problems.append(f"{exp_id}: initial_state {init_state!r} is not a valid state")
            for dep in exp.get("depends_on", []):
                if dep not in self.experiments:
                    problems.append(f"{exp_id}: depends_on unknown experiment {dep!r}")

        problems.extend(self._check_phase_ordering())
        problems.extend(self._check_dependency_acyclic_and_consistent())
        return problems

    def _check_phase_ordering(self) -> List[str]:
        """The scientific chain II-A -> II-B -> II-C must hold: no experiment
        in a later phase may be a dependency of (or have no path from) the
        prior phase's exit point; concretely, each phase's first experiment
        must depend (directly or transitively) on every experiment of the
        previous phase in phase_order."""
        problems: List[str] = []
        phase_to_exps = {p["id"]: p.get("experiments", []) for p in self.phases}
        for i in range(1, len(self.phase_order)):
            prev_phase = self.phase_order[i - 1]
            cur_phase = self.phase_order[i]
            prev_exps = set(phase_to_exps.get(prev_phase, []))
            cur_first = (phase_to_exps.get(cur_phase, []) or [None])[0]
            if cur_first is None:
                continue
            reachable = self._transitive_deps(cur_first)
            missing = prev_exps - reachable
            if missing:
                problems.append(
                    f"phase ordering violated: {cur_first} (phase {cur_phase}) does not "
                    f"transitively depend on {sorted(missing)} (phase {prev_phase})")
        return problems

    def _transitive_deps(self, exp_id: str, _seen: Optional[set] = None) -> set:
        _seen = _seen if _seen is not None else set()
        for dep in self.depends_on(exp_id):
            if dep not in _seen:
                _seen.add(dep)
                self._transitive_deps(dep, _seen)
        return _seen

    def _check_dependency_acyclic_and_consistent(self) -> List[str]:
        problems: List[str] = []
        visiting: set = set()
        done: set = set()

        def visit(exp_id: str, stack: List[str]):
            if exp_id in done:
                return
            if exp_id in visiting:
                problems.append(f"dependency cycle: {' -> '.join(stack + [exp_id])}")
                return
            visiting.add(exp_id)
            for dep in self.depends_on(exp_id):
                if dep in self.experiments:
                    visit(dep, stack + [exp_id])
            visiting.discard(exp_id)
            done.add(exp_id)

        for exp_id in self.experiments:
            visit(exp_id, [])
        return problems


def load(path: Optional[str | Path] = None) -> Roadmap:
    """Load research2/roadmap.json. Raises RoadmapError on missing/malformed
    file (not on validation problems -- call .validate() for those)."""
    p = Path(path) if path else default_path()
    if not p.exists():
        raise RoadmapError(f"roadmap not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RoadmapError(f"roadmap is not valid JSON ({p}): {e}") from e
    if not isinstance(data, dict):
        raise RoadmapError(f"roadmap root must be a JSON object: {p}")
    return Roadmap(data, p)


def default_path() -> Path:
    return Path(__file__).resolve().parents[2] / "research2" / "roadmap.json"
