#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_server.py: a small local web UI for demonstrating the TEM Agent's
optimisation loops, with or without the simulator.

It runs the same entry point the CLI does (invoke_tem_ai_model with the
deterministic 'rule' policy), captures the agent's own stdout, and streams it to
the page. Nothing about the agent changes: the page shows what the agent
actually printed.

    python demo_server.py                 # http://127.0.0.1:8765
    python demo_server.py --port 9000 --host 0.0.0.0

Standard library only. The one endpoint that touches the network is
/api/simcheck, which probes the AutoScript simulator the page is pointed at.
"""

import argparse
import contextlib
import io
import json
import os
import re
import socket
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from invoke_tem_ai_model import invoke_tem_ai_model  # noqa: E402

MAX_ITERATIONS = 60          # tomography needs ~35; the CLI default of 40 is tight
DEFAULT_HOST = "13.202.106.11"
DEFAULT_PORT = 7521

LOOPS = [
    {
        "id": "focus",
        "title": "Focus sweep",
        "blurb": "One defocus sweep across seven points around the current focus, "
                 "each frame scored by normalized variance, then the peak restored.",
    },
    {
        "id": "focus2",
        "title": "Focus sweep, two-stage",
        "blurb": "A coarse seven-point sweep, then a finer five-point pass around "
                 "the coarse peak.",
    },
    {
        "id": "tomography",
        "title": "Tomography tilt series",
        "blurb": "Tilt to each angle in turn, refocus at every angle, acquire a "
                 "projection, then restore the stage to zero.",
    },
    {
        "id": "survey",
        "title": "Survey",
        "blurb": "Read the microscope state, open the column valve, unblank, select "
                 "STEM, configure the detector, take one image.",
    },
]

# Lines the rule policy prints, turned back into numbers for the page's chart.
FOCUS_RE = re.compile(r"\[RulePolicy\]\s+([+-]?\d+(?:\.\d+)?) nm -> (?:normalized variance )?([0-9.]+)\s*$")
TOMO_RE = re.compile(r"\[RulePolicy\]\s+tilt\s+([+-]?\d+) deg,\s+([+-]?\d+(?:\.\d+)?) nm -> ([0-9.]+)")
BEST_DF_RE = re.compile(r"Best observed defocus:\s+([+-]?\d+(?:\.\d+)?) nm")
BEST_NV_RE = re.compile(r"Best observed normalized variance:\s+([0-9.]+)")
ITER_RE = re.compile(r"Total iterations:\s+(\d+)")


class Run:
    """One loop execution, with the agent's output kept for the page to poll."""

    def __init__(self, loop, dry_run, host, port):
        self.id = uuid.uuid4().hex[:12]
        self.loop = loop
        self.dry_run = dry_run
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.lines = []
        self.points = []
        self.status = "running"
        self.best = None
        self.iterations = None
        self.error = None

    def add_line(self, line):
        with self.lock:
            self.lines.append(line)
            m = TOMO_RE.search(line)
            if m:
                self.points.append({"tilt": int(m.group(1)), "df_nm": float(m.group(2)),
                                    "score": float(m.group(3))})
            else:
                m = FOCUS_RE.search(line)
                if m:
                    self.points.append({"tilt": None, "df_nm": float(m.group(1)),
                                        "score": float(m.group(2))})
            m = BEST_DF_RE.search(line)
            if m:
                self.best = {"df_nm": float(m.group(1)), "score": (self.best or {}).get("score")}
            m = BEST_NV_RE.search(line)
            if m and self.best:
                self.best["score"] = float(m.group(1))
            m = ITER_RE.search(line)
            if m:
                self.iterations = int(m.group(1))
            if "TASK COMPLETE" in line:
                self.status = "done"

    def snapshot(self, since):
        with self.lock:
            return {
                "id": self.id, "loop": self.loop, "dry_run": self.dry_run,
                "host": self.host, "port": self.port, "status": self.status,
                "lines": self.lines[since:], "next": len(self.lines),
                "points": list(self.points), "best": self.best,
                "iterations": self.iterations, "error": self.error,
            }

    def fail(self, text):
        with self.lock:
            self.status = "failed"
            self.error = text


class RunBuffer(io.TextIOBase):
    """Stdout sink: every complete line goes to the run as it is printed."""

    def __init__(self, run):
        self.run = run
        self._partial = ""

    def write(self, text):
        self._partial += text
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            self.run.add_line(line)
        return len(text)

    def flush(self):
        if self._partial:
            self.run.add_line(self._partial)
            self._partial = ""

    def isatty(self):
        return False


RUNS = {}
RUNS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()   # stdout capture is process-global: one loop at a time


def execute(run):
    buffer = RunBuffer(run)
    previous_host = os.environ.get("AUTOSCRIPT_HOST")
    previous_port = os.environ.get("AUTOSCRIPT_PORT")
    os.environ["AUTOSCRIPT_HOST"] = run.host
    os.environ["AUTOSCRIPT_PORT"] = str(run.port)
    try:
        with contextlib.redirect_stdout(buffer):
            invoke_tem_ai_model(
                model_name="rule", goal_text=run.loop,
                max_iterations=MAX_ITERATIONS, dry_run=run.dry_run,
                tools_config=None, instrument_config=None,
            )
        buffer.flush()
        if run.status == "running":
            run.status = "stopped"
    except Exception:
        run.fail(traceback.format_exc(limit=4))
    finally:
        for key, value in (("AUTOSCRIPT_HOST", previous_host),
                           ("AUTOSCRIPT_PORT", previous_port)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def sim_check(host, port):
    """TCP reach, then one AutoScript call to see whether the product is activated."""
    result = {"host": host, "port": port, "tcp": False, "activation": None, "detail": ""}
    probe = socket.socket()
    probe.settimeout(3.0)
    try:
        probe.connect((host, int(port)))
        result["tcp"] = True
    except Exception as e:
        result["detail"] = f"TCP connect failed: {e}"
        return result
    finally:
        probe.close()

    try:
        from autoscript_tem_microscope_client import TemMicroscopeClient
        scope = TemMicroscopeClient()
        scope.connect(host, int(port))
        try:
            mode = scope.optics.optical_mode
            result["activation"] = "ok"
            result["detail"] = f"answered, optical mode {mode!r}"
        finally:
            scope.disconnect()
    except Exception as e:
        result["activation"] = "refused"
        result["detail"] = str(e)
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "tem-agent-demo"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = (ROOT / "web" / "index.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/api/loops":
            return self._send(200, {"loops": LOOPS, "default_host": DEFAULT_HOST,
                                    "default_port": DEFAULT_PORT,
                                    "max_iterations": MAX_ITERATIONS})
        if path == "/api/state":
            run_id = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
            since = int((parse_qs(urlparse(self.path).query).get("since") or ["0"])[0])
            run = RUNS.get(run_id)
            if run is None:
                return self._send(404, {"error": "unknown run"})
            return self._send(200, run.snapshot(since))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/run":
            loop = str(body.get("loop") or "focus")
            if loop not in {entry["id"] for entry in LOOPS}:
                return self._send(400, {"error": f"unknown loop {loop!r}"})
            run = Run(loop, bool(body.get("dry_run", True)),
                      str(body.get("host") or DEFAULT_HOST),
                      int(body.get("port") or DEFAULT_PORT))
            with RUNS_LOCK:
                RUNS[run.id] = run
            threading.Thread(target=lambda: (RUN_LOCK.acquire(), execute(run),
                                             RUN_LOCK.release()), daemon=True).start()
            return self._send(200, {"id": run.id})

        if path == "/api/simcheck":
            host = str(body.get("host") or DEFAULT_HOST)
            port = int(body.get("port") or DEFAULT_PORT)
            return self._send(200, sim_check(host, port))

        return self._send(404, {"error": "not found"})


def main():
    parser = argparse.ArgumentParser(description="Local demo UI for the TEM Agent.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="port to bind (default 8765)")
    args = parser.parse_args()

    if not (ROOT / "web" / "index.html").exists():
        print(f"missing {ROOT / 'web' / 'index.html'}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TEM Agent demo UI on http://{args.host}:{args.port}")
    print("loops: " + ", ".join(entry["id"] for entry in LOOPS))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
