"""R2-08 prerequisites (fail closed, verified every run).

Requires: R2-01 PASS (trace identity binding / canonical coding-v1 seed-42),
R2-04 PASS (II-A closed), R2-05 PASS, R2-06 PASS, R2-07 PASS. Reuses the
existing chain-verification pattern and re-verifies the canonical R2-01 trace
is byte-identical and matches the documented R2-08 reuse identity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .. import store
from ..r2_07 import prerequisite as r2_07_prerequisite
from ..r2_01 import trace_contract

STATUS_OK = "OK"
STATUS_UPSTREAM_PREREQUISITE_FAILED = "UPSTREAM_PREREQUISITE_FAILED"
STATUS_REUSED_TRACE_MISMATCH = "REUSED_TRACE_MISMATCH"
STATUS_REUSED_TRACE_MISSING = "REUSED_TRACE_MISSING"

# Existing canonical R2-01 trace reused as coding-v1 / seed-42 (task spec).
REUSED_R2_01_RUN_ID = "bb6d8ca34dbc4537a43d54be30c9f3e1"
REUSED_CAPTURE_SOURCE = "286a41477ce54d5fb9ccc24f4e508f22"
REUSED_TRACE_SHA256 = "e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1"


@dataclass
class R208Binding:
    status: str
    message: str
    r2_01_run_id: Optional[str] = None
    r2_04_run_id: Optional[str] = None
    r2_05_run_id: Optional[str] = None
    r2_06_run_id: Optional[str] = None
    r2_07_run_id: Optional[str] = None
    reused_trace_path: Optional[str] = None
    reused_trace_sha256: Optional[str] = None
    reused_capture_source: Optional[str] = None
    candidate_status: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def require_full_chain_pass(root: Optional[Path] = None) -> R208Binding:
    upstream = r2_07_prerequisite.require_full_chain_pass(root)
    if not upstream.ok:
        return R208Binding(STATUS_UPSTREAM_PREREQUISITE_FAILED,
                           f"R2-01/R2-04/R2-05/R2-06/R2-07 prerequisite not met: {upstream.message}")

    if upstream.r2_01_run_id != REUSED_R2_01_RUN_ID:
        return R208Binding(STATUS_REUSED_TRACE_MISMATCH,
                           f"current R2-01 PASS binding run_id={upstream.r2_01_run_id!r} does not "
                           f"match the canonical coding-v1/seed-42 reuse identity "
                           f"{REUSED_R2_01_RUN_ID!r}; refusing to guess.",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id, r2_06_run_id=upstream.r2_06_run_id,
                           r2_07_run_id=upstream.r2_07_run_id, candidate_status=upstream.candidate_status)

    trace_path = Path(upstream.trace_path)
    if not trace_path.is_file():
        return R208Binding(STATUS_REUSED_TRACE_MISSING,
                           f"canonical reused R2-01 trace is missing at {trace_path}",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id, r2_06_run_id=upstream.r2_06_run_id,
                           r2_07_run_id=upstream.r2_07_run_id, reused_trace_path=str(trace_path),
                           reused_trace_sha256=upstream.trace_sha256, reused_capture_source=REUSED_CAPTURE_SOURCE,
                           candidate_status=upstream.candidate_status)

    actual = trace_contract.sha256_file(trace_path)["sha256"]
    if actual != REUSED_TRACE_SHA256 or upstream.trace_sha256 != REUSED_TRACE_SHA256:
        return R208Binding(STATUS_REUSED_TRACE_MISMATCH,
                           f"canonical reused R2-01 trace SHA mismatch: expected {REUSED_TRACE_SHA256}, "
                           f"binding recorded {upstream.trace_sha256}, on-disk {actual}",
                           r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                           r2_05_run_id=upstream.r2_05_run_id, r2_06_run_id=upstream.r2_06_run_id,
                           r2_07_run_id=upstream.r2_07_run_id, reused_trace_path=str(trace_path),
                           reused_trace_sha256=actual, reused_capture_source=REUSED_CAPTURE_SOURCE,
                           candidate_status=upstream.candidate_status)

    return R208Binding(STATUS_OK,
                       f"R2-01/04/05/06/07 PASS verified; coding-v1/seed-42 reuses canonical R2-01 "
                       f"run {REUSED_R2_01_RUN_ID} trace {REUSED_TRACE_SHA256} (capture source "
                       f"{REUSED_CAPTURE_SOURCE}), byte-identical on disk.",
                       r2_01_run_id=upstream.r2_01_run_id, r2_04_run_id=upstream.r2_04_run_id,
                       r2_05_run_id=upstream.r2_05_run_id, r2_06_run_id=upstream.r2_06_run_id,
                       r2_07_run_id=upstream.r2_07_run_id, reused_trace_path=str(trace_path),
                       reused_trace_sha256=actual, reused_capture_source=REUSED_CAPTURE_SOURCE,
                       candidate_status=upstream.candidate_status)
