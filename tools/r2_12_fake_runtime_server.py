"""Fake llama-server.exe stand-in, used ONLY by
tools/r2_12_benchmark_driver_selftest.py to exercise the real execution
lifecycle in tools/r2_12_benchmark_driver.py without touching the real 30B
model or the real runtime binary. Never imported by, or reachable from,
--execute against the real runtime.

Emulates exactly the two stderr markers and the two HTTP endpoints that
_execute_one_launch() actually depends on:
  - stderr: "argine: retention policy attiva: ..." then
            "ARGINE_RETENTION_TRACE: aperta con successo: ..."
  - GET /health -> {"status": "ok"}
  - POST /completion (stream=true) -> SSE "data: {...}" chunks, final
    chunk carries "stop": true and a "timings" object with predicted_n/
    predicted_per_second/predicted_ms, matching llama.cpp's real schema.

Usage: python3 r2_12_fake_runtime_server.py -m <model> --n-gpu-layers 0
       --port <port> --host 127.0.0.1 --ctx-size <n>
Mode, set via env FAKE_RUNTIME_MODE:
  "ok"        (default) -- normal successful run.
  "no_marker" -- never emits the readiness markers (exercises timeout/failure path).
  "fixed_text:<text>" -- always returns <text> as the completion content
                         (used to construct output-equivalence match/mismatch cases).
"""
import http.server
import json
import os
import sys
import threading
import time

PORT = None
for i, a in enumerate(sys.argv):
    if a == "--port" and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])
if PORT is None:
    print("fake runtime: no --port given", file=sys.stderr)
    sys.exit(2)

MODE = os.environ.get("FAKE_RUNTIME_MODE", "ok")
FIXED_TEXT = None
if MODE.startswith("fixed_text:"):
    FIXED_TEXT = MODE[len("fixed_text:"):]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/completion":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        n_predict = int(req.get("n_predict", 8))
        text = FIXED_TEXT if FIXED_TEXT is not None else f"fake-output-{req.get('prompt', '')[:16]}"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {"content": text, "stop": False}
        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
        self.wfile.flush()
        time.sleep(0.05)
        final = {
            "content": "", "stop": True,
            "timings": {
                "predicted_n": n_predict, "predicted_ms": 50.0,
                "predicted_per_second": n_predict / 0.05 if n_predict else 0.0,
                "prompt_n": 8, "prompt_ms": 5.0,
            },
        }
        self.wfile.write(("data: " + json.dumps(final) + "\n\n").encode())
        self.wfile.flush()


def main():
    print("fake-runtime: starting", file=sys.stderr)
    sys.stderr.flush()
    if MODE != "no_marker":
        print("argine: retention policy attiva: fake (R2-12 fake-runtime selftest)", file=sys.stderr)
        sys.stderr.flush()
        time.sleep(0.05)
        print("argine: ARGINE_RETENTION_TRACE: aperta con successo: /tmp/fake.trace.jsonl", file=sys.stderr)
        sys.stderr.flush()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
