"""R2-08 frozen workload family definitions and provenance.

Four canonical workload families, each with:
  - workload_id, version, immutable source path, SHA-256, semantic category,
    description.

`coding-v1` reuses the canonical Research Part 1 / R2-01 coding workload
(`workloads/v1/coding.json`, dev split). The other three families are frozen
Research2 R2-08 definitions committed under `research2/workloads/r2_08/`.

Prompt content is frozen before any real capture. These definitions are used by
capture planning and provenance/fingerprint binding; implementation-time tests
use synthetic traces and never execute real captures.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


REPO_ROOT = Path(__file__).resolve().parents[3]
R2_08_WORKLOAD_ROOT = REPO_ROOT / "research2" / "workloads" / "r2_08"


@dataclass(frozen=True)
class WorkloadDef:
    workload_id: str
    version: str
    source_path: str          # repo-relative, POSIX-ish in JSON
    sha256: str
    semantic_category: str
    description: str
    # capture parameters (compatible with argine research trace)
    workload_arg: str         # --workload value (name or path)
    workload_version_arg: str # --workload-version value (when applicable)
    split: str
    n_predict: int


def _sha256_repo(rel: str) -> str:
    return hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()


# Canonical R2-01 coding workload reused as coding-v1 / seed-42.
CODING_V1 = WorkloadDef(
    workload_id="coding-v1",
    version="v1",
    source_path="workloads/v1/coding.json",
    sha256="460949e7abf004374bb0b06dcda89eeaba0e916b5f6053784afe037e6f2162d8",
    semantic_category="coding",
    description="Existing canonical R2-01 coding workload (workloads/v1/coding.json, dev split), reused as coding-v1 replicate seed-42 and captured as seed-43 under the same protocol.",
    workload_arg="coding",
    workload_version_arg="v1",
    split="dev",
    n_predict=128,
)

REASONING_V1 = WorkloadDef(
    workload_id="reasoning-v1",
    version="r2-08-v1",
    source_path="research2/workloads/r2_08/reasoning-v1.json",
    sha256=_sha256_repo("research2/workloads/r2_08/reasoning-v1.json"),
    semantic_category="reasoning",
    description="Fixed analytical/multi-step reasoning task designed to generate a substantial decode sequence (frozen R2-08).",
    workload_arg=str(R2_08_WORKLOAD_ROOT / "reasoning-v1.json"),
    workload_version_arg="r2-08-v1",
    split="dev",
    n_predict=512,
)

SUMMARIZATION_V1 = WorkloadDef(
    workload_id="summarization-v1",
    version="r2-08-v1",
    source_path="research2/workloads/r2_08/summarization-v1.json",
    sha256=_sha256_repo("research2/workloads/r2_08/summarization-v1.json"),
    semantic_category="summarization",
    description="Fixed long technical source input followed by detailed summarization/synthesis; source committed locally, no web/network dependency (frozen R2-08).",
    workload_arg=str(R2_08_WORKLOAD_ROOT / "summarization-v1.json"),
    workload_version_arg="r2-08-v1",
    split="dev",
    n_predict=512,
)

SUMMARIZATION_SOURCE_PATH = "research2/workloads/r2_08/sources/summarization_technical_source.txt"
SUMMARIZATION_SOURCE_SHA256 = _sha256_repo(SUMMARIZATION_SOURCE_PATH)

INSTRUCTION_V1 = WorkloadDef(
    workload_id="instruction-v1",
    version="r2-08-v1",
    source_path="research2/workloads/r2_08/instruction-v1.json",
    sha256=_sha256_repo("research2/workloads/r2_08/instruction-v1.json"),
    semantic_category="instruction",
    description="Fixed general expository/instruction-following task, materially different from coding, explicit reasoning, and summarization (frozen R2-08).",
    workload_arg=str(R2_08_WORKLOAD_ROOT / "instruction-v1.json"),
    workload_version_arg="r2-08-v1",
    split="dev",
    n_predict=512,
)

CANONICAL_WORKLOADS: List[WorkloadDef] = [CODING_V1, REASONING_V1, SUMMARIZATION_V1, INSTRUCTION_V1]
CANONICAL_WORKLOAD_IDS = [w.workload_id for w in CANONICAL_WORKLOADS]
WORKLOADS_BY_ID: Dict[str, WorkloadDef] = {w.workload_id: w for w in CANONICAL_WORKLOADS}

SEEDS = (42, 43)
CANONICAL_CELLS = [(wid, seed) for wid in CANONICAL_WORKLOAD_IDS for seed in SEEDS]
REUSED_CELL = ("coding-v1", 42)


def workload_manifest_json() -> Dict[str, object]:
    return {
        "schema_version": 1,
        "workloads": [
            {
                "workload_id": w.workload_id,
                "version": w.version,
                "source_path": w.source_path,
                "sha256": w.sha256,
                "semantic_category": w.semantic_category,
                "description": w.description,
                "split": w.split,
                "n_predict": w.n_predict,
            }
            for w in CANONICAL_WORKLOADS
        ],
        "summarization_source_input": {
            "path": SUMMARIZATION_SOURCE_PATH,
            "sha256": SUMMARIZATION_SOURCE_SHA256,
            "committed_locally": True,
            "web_network_dependency": False,
        },
        "canonical_cells": [{"workload_id": wid, "seed": seed} for wid, seed in CANONICAL_CELLS],
        "reused_cell": {"workload_id": REUSED_CELL[0], "seed": REUSED_CELL[1],
                        "source": "R2-01 canonical trace (coding-v1 / seed-42)"},
    }
