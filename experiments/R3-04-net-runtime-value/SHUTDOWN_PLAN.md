# R3-04 shutdown plan (Part K)

## What the evidence shows
* R3-03B took ~7.36 h (planned ~2.85 h). Shutdown latency accounts for most of the excess (sum of recorded `shutdown_wait_s` 16,588 s; about 4.2 h above the planned 20 s per launch).
* From launch 9 on, 71 of 81 launches log `cleaning up before exit` at **~300.0 s of server uptime**, even when the request finished earlier; in the conformance launches the same line appears at 299.7-300.0 s although the release was at 260-285 s. Launches 1-8 and 17 exited earlier and with irregular latency (4.8-151.6 s).
* Pinned server source (llama.cpp `254b1773`): the Windows handler is `SetConsoleCtrlHandler -> signal_handler -> shutdown_handler -> mcp_mgr.shutdown (flag) + ctx_server.terminate()`; `start_loop` re-checks `running` every 1 s; `clean_up` (the line seen in the log) runs right after `start_loop` returns. No wait, join, timer or sleep of 300 s exists on that path; the only 300 s constant is the stream-session TTL (GC interval 60 s), which `stop_gc` wakes via a condition variable and which is reached *after* the observed line. Argine patches contain no 300 s wait.
* Therefore the delay is **before** the handler runs: i.e. delivery of the console Ctrl-C event to the child. The driver sends it with `Popen.send_signal(CTRL_C_EVENT)` to a child that is **not** a process-group leader while the driver ignores SIGINT; `GenerateConsoleCtrlEvent` with such a pid is not a defined per-process delivery.

## Diagnosis status: UNDETERMINED (plausible candidate only)
No artifact shows why the event is delivered at 300 s uptime. No cause is asserted. Candidates, none verified: (H1) console Ctrl-event delivery semantics for a non-group-leader child; (H2) host-side stall specific to llama-server (I/O or memory pressure). H1 can be tested without a model; H2 cannot.

## Tooling-only change (no source change, no decode change)
`tools/r3_04_shutdown.py`: start the runtime in its **own console** (`CREATE_NEW_CONSOLE`; stdout/stderr are already redirected to files) and deliver the event from a short helper that `AttachConsole(pid)`, ignores the event for itself, calls `GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` and `FreeConsole()`. The event then reaches only the server's console; the driver no longer ignores SIGINT. Fallback order: legacy R3-03B `send_signal` path -> `terminate()` after the graceful timeout (600 s, as in amendment 1), always recorded as `shutdown_mode`.
Invariants (tested offline with fake processes): the shutdown request is issued only after the HTTP response is complete; the timing line is still required (a launch without `policy_timing` is not admitted); nothing is terminated before the graceful timeout; `terminate_fallback` stays a failure; no silent change of `shutdown_mode` semantics.
Requires a **new acquisition-tooling commit** (done in the tooling commit) and is not part of the scientific freeze.

## Validation before counted acquisition (small, explicit user authorisation)
1. **Model-free probe** (`tools/r3_04_shutdown_probe.py`): a stand-in console child with a Ctrl-C handler; 20 trials each of legacy delivery vs the new helper; reports latency distributions. If legacy reproduces the heavy tail and the helper does not, H1 is supported and the fix is validated. If neither shows a tail, H1 is not supported (no conclusion about H2).
2. **In-campaign measurement**: the 10 mechanism launches use the new path and record release->cleanup latency. Success criterion fixed now: no launch above 60 s and no 300 s plateau. If it fails, the timing campaign uses the R3-03B path unchanged with its full time budget.

## Time (Part P)
See SAMPLE_SIZE.json `hours`: 19.2 model-bearing hours with the observed 300 s floor, 15.8 h if the floor disappears. At 1.5 GiB the floor matters little (launches are longer), so the primary campaign (9.8 h vs 9.5 h) is barely affected; the saving is mostly in the 4 and 8 GiB campaigns.
