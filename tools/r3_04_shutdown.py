"""R3-04 shutdown tooling (tooling only: no runtime source change, no change to the measured decode).

Graceful stop of llama-server so the destructor runs and the single `argine: policy_timing` line is emitted.
Method "own_console_helper" (candidate fix, requires validation): the server runs in its OWN console (CREATE_NEW_CONSOLE) and a short
helper process attaches to that console and generates CTRL_C_EVENT for it, so the event reaches only the server and the driver does not
have to ignore SIGINT. Method "legacy" is the R3-03B delivery (Popen.send_signal(CTRL_C_EVENT)). Fallback: terminate after the graceful
timeout, always reported as shutdown_mode "terminate_fallback" (a failure for admission, exactly as in R3-03B).
The request is only ever issued by the caller AFTER the HTTP response is complete.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Optional

GRACEFUL_TIMEOUT_S = 600          # amendment 1 value, unchanged
CREATE_NEW_CONSOLE = 0x00000010
METHODS = ("legacy", "own_console_helper")

HELPER_SOURCE = r'''
import ctypes, sys
k = ctypes.windll.kernel32
pid = int(sys.argv[1])
k.FreeConsole()
if not k.AttachConsole(pid):
    sys.exit(2)
k.SetConsoleCtrlHandler(None, True)          # this helper must not react to its own event
ok = k.GenerateConsoleCtrlEvent(0, 0)        # CTRL_C_EVENT to the processes attached to this (the server's own) console
k.FreeConsole()
sys.exit(0 if ok else 3)
'''


def popen_creationflags(method: str) -> int:
    if method not in METHODS:
        raise ValueError(f"unknown shutdown method {method!r}")
    return CREATE_NEW_CONSOLE if (method == "own_console_helper" and os.name == "nt") else 0


def _legacy_request(proc) -> None:
    old = None
    try:
        if os.name == "nt":
            old = signal.signal(signal.SIGINT, signal.SIG_IGN)        # the console event is broadcast to this process too
            proc.send_signal(signal.CTRL_C_EVENT)                     # type: ignore[attr-defined]
        else:
            proc.send_signal(signal.SIGINT)
        return
    except BaseException:
        if old is not None:
            signal.signal(signal.SIGINT, old)
        raise


def stop_server(proc, timeout_s: float = GRACEFUL_TIMEOUT_S, method: str = "legacy", *, runner: Callable[..., Any] = subprocess.run,
                now: Callable[[], float] = time.monotonic, helper_cmd: Optional[list] = None) -> Dict[str, Any]:
    """Returns {mode, method_used, latency_s, helper_returncode}. mode in already_exited | graceful | terminate_fallback."""
    if method not in METHODS:
        raise ValueError(f"unknown shutdown method {method!r}")
    if proc.poll() is not None:
        return {"mode": "already_exited", "method_used": None, "latency_s": 0.0, "helper_returncode": None}
    t0 = now()
    used, hrc = method, None
    old = None
    try:
        if method == "own_console_helper" and os.name == "nt":
            r = runner(helper_cmd or [sys.executable, "-c", HELPER_SOURCE, str(proc.pid)], timeout=30)
            hrc = getattr(r, "returncode", None)
            if hrc != 0:                                              # helper failed: recorded, then the proven R3-03B path
                used = "legacy_after_helper_failure"
                old = signal.signal(signal.SIGINT, signal.SIG_IGN) if os.name == "nt" else None
                proc.send_signal(signal.CTRL_C_EVENT)                 # type: ignore[attr-defined]
        else:
            if os.name == "nt":
                old = signal.signal(signal.SIGINT, signal.SIG_IGN)
                proc.send_signal(signal.CTRL_C_EVENT)                 # type: ignore[attr-defined]
            else:
                proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=timeout_s)
            return {"mode": "graceful", "method_used": used, "latency_s": now() - t0, "helper_returncode": hrc}
        except subprocess.TimeoutExpired:
            pass
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)
    proc.terminate()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=60)
    return {"mode": "terminate_fallback", "method_used": used, "latency_s": now() - t0, "helper_returncode": hrc}
