# R3-04 Gate 2 — independent audit and freeze (additive evidence)

Gate 2 = the 10 trace-enabled mechanism/conformance launches (frozen schedule hash 71a8d474…b63f), run on the host with `--shutdown-method own_console_helper`.
Protocol freeze a88359b5… (commit 4a0aa2d), Gate 1 freeze 6379572…d6f42 (d7e8f38) and Gate 2 helper tooling 2345808 are unchanged.

Classification: **R3_04_MECHANISM_PASS**, recorded separately with **R3_04_SHUTDOWN_HELPER_VALIDATED_ON_REAL_MODEL**.

* `audit_gate2.py --build <host run dir>` re-reads the ORIGINAL artifacts (records, raw stderr logs, gate inputs, file mtimes) and runs 30 independent checks (`AUDIT.json`, all pass); `--verify` re-checks the committed evidence offline.
* `records_sanitised/` are the 10 compact records without the host path field (`stderr_path`); raw stderr logs (host paths) are not committed — `ORIGINAL_FILE_SHA256.json` holds the sha256 of every original.
* `MECHANISM_METRICS.json`: per-launch counters and A/S/P contrasts (mechanism only; no wall-time or throughput claim, no NET_WIN/NET_LOSS).
* `LAUNCH_TIMING.json`: per-launch durations reconstructed from log stamps and file mtimes (traced launches; NOT the timing-campaign launch durations).

Not verified here: sha256 of the model and expert-store files (`C:\modelli` was not reachable from this session); identity is checked by frozen path, store layout and budget in each log and by the frozen output-token digest.
