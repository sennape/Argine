"""R2-08 persistent 8-cell capture matrix (resumable, atomic registration).

Stored outside Git under the Research2 evidence root for R2-08. A capture is
canonical only after successful completion, trace existence/parsing, schema and
identity/configuration validation. Failed/incomplete captures never enter
canonical analysis.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import store
from . import workloads

MATRIX_FILENAME = "capture_matrix.json"
ALLOWED_FINGERPRINT_DIFF_KEYS = {
    "workload_id", "workload_version", "workload_sha256", "seed", "prompt_sha256",
    "cell_id",
}


@dataclass
class CaptureCell:
    workload_id: str
    workload_version: str
    seed: int
    prompt_sha256: str
    model_sha256: str
    runtime_sha256: str
    configuration_fingerprint: str
    configuration: Dict[str, Any]
    capture_run_id: Optional[str] = None
    capture_source: Optional[str] = None
    trace_path: Optional[str] = None
    trace_sha256: Optional[str] = None
    validation_state: str = "MISSING"   # MISSING | INVALID | VALID
    canonical_status: bool = False
    reused: bool = False
    note: str = ""

    @property
    def cell_id(self) -> str:
        return f"{self.workload_id}/seed-{self.seed}"

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cell_id"] = self.cell_id
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "CaptureCell":
        d = dict(d)
        d.pop("cell_id", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def matrix_path(root: Optional[Path] = None) -> Path:
    return (root or store.evidence_root()) / "R2-08" / MATRIX_FILENAME


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_matrix(root: Optional[Path] = None) -> Dict[Tuple[str, int], CaptureCell]:
    data = _read_json(matrix_path(root), {"cells": []})
    out: Dict[Tuple[str, int], CaptureCell] = {}
    for item in data.get("cells", []):
        cell = CaptureCell.from_json(item)
        out[(cell.workload_id, cell.seed)] = cell
    return out


def save_matrix(cells: Dict[Tuple[str, int], CaptureCell], root: Optional[Path] = None) -> Path:
    ordered = [cells[k].to_json() for k in sorted(cells)]
    p = matrix_path(root)
    _write_json(p, {"schema_version": 1, "cells": ordered})
    return p


def upsert_cell(cell: CaptureCell, root: Optional[Path] = None) -> Path:
    cells = load_matrix(root)
    cells[(cell.workload_id, cell.seed)] = cell
    return save_matrix(cells, root)


def expected_canonical_cells() -> List[Tuple[str, int]]:
    return list(workloads.CANONICAL_CELLS)


def missing_or_invalid_cells(cells: Dict[Tuple[str, int], CaptureCell]) -> List[Tuple[str, int]]:
    missing = []
    for key in expected_canonical_cells():
        cell = cells.get(key)
        if cell is None or not (cell.canonical_status and cell.validation_state == "VALID"):
            missing.append(key)
    return missing


def all_canonical_valid(cells: Dict[Tuple[str, int], CaptureCell]) -> bool:
    return len(missing_or_invalid_cells(cells)) == 0 and len(cells) >= len(expected_canonical_cells())


def configuration_fingerprint_dict(*, model_sha256: str, runtime_sha256: str,
                                   model_name: str, runtime_args: Dict[str, Any],
                                   expert_budget: str, ctx: int, threads: int,
                                   threads_batch: int, sampling: Dict[str, Any],
                                   trace_schema: str, instrumentation: Dict[str, Any],
                                   workload_id: str, workload_version: str,
                                   workload_sha256: str, seed: int,
                                   prompt_sha256: str) -> Dict[str, Any]:
    return {
        "model_sha256": model_sha256,
        "runtime_sha256": runtime_sha256,
        "model_name": model_name,
        "runtime_args": dict(runtime_args),
        "expert_budget": expert_budget,
        "ctx": ctx,
        "generation_threads": threads,
        "batch_threads": threads_batch,
        "sampling": dict(sampling),
        "trace_schema": trace_schema,
        "instrumentation": dict(instrumentation),
        "workload_id": workload_id,
        "workload_version": workload_version,
        "workload_sha256": workload_sha256,
        "seed": seed,
        "prompt_sha256": prompt_sha256,
    }


def fingerprint_hash(fp: Dict[str, Any]) -> str:
    import hashlib
    blob = json.dumps(fp, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _sampling_compatible(base_s: Any, other_s: Any, base_seed: Any, other_seed: Any) -> bool:
    if not isinstance(base_s, dict) or not isinstance(other_s, dict):
        return base_s == other_s
    # sampling may differ only in seed/replicate markers
    allowed = {"seed"}
    for k in set(base_s) | set(other_s):
        if k in allowed:
            continue
        if base_s.get(k) != other_s.get(k):
            return False
    return True


def compare_fingerprint_compatibility(fps: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    fps = list(fps)
    if not fps:
        return {"compatible": False, "reason": "no fingerprints"}
    base = dict(fps[0])
    for other in fps[1:]:
        for k in set(base) | set(other):
            if k in ALLOWED_FINGERPRINT_DIFF_KEYS:
                continue
            if k == "sampling":
                if not _sampling_compatible(base.get(k), other.get(k), base.get("seed"), other.get("seed")):
                    return {"compatible": False, "reason": "fingerprint drift outside allowed keys: sampling",
                            "base": base.get(k), "other": other.get(k)}
                continue
            if base.get(k) != other.get(k):
                return {"compatible": False, "reason": f"fingerprint drift outside allowed keys: {k}",
                        "base": base.get(k), "other": other.get(k)}
    return {"compatible": True, "reason": "only workload/prompt/seed differ"}
