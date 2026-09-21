#!/usr/bin/env python3
"""Local setup and operations dashboard for Omni-Assistant."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "dashboard"
ENV_FILE = ROOT / ".env"

if ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)
    except ImportError:
        pass

HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
TOKEN = os.getenv("DASHBOARD_TOKEN", "")
SENSITIVE_KEYS = {"LLM_API_KEY", "NAPCAT_TOKEN", "AGY_INSTALL_COMMAND"}
ALLOWED_KEYS = {
    "ENABLE_QQ", "ENABLE_WECHAT", "AI_PROVIDER", "AUTO_INSTALL_CLI",
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TEMPERATURE",
    "CODEX_BIN_PATH", "CODEX_ARGS", "OPENCODE_BIN_PATH", "OPENCODE_ARGS",
    "CLAUDE_BIN_PATH", "CLAUDE_ARGS", "AGY_BIN_PATH", "AGY_ARGS",
    "AGY_INSTALL_COMMAND", "ADMIN_QQ", "TARGET_GROUP_IDS",
    "NAPCAT_HTTP_URL", "NAPCAT_WS_URL", "NAPCAT_TOKEN", "WECHAT_BASE_URL",
    "WECHAT_DATA_DIR", "ENABLE_MS_TODO", "MS_TODO_DEFAULT_LIST_ID",
    "MS_TODO_AUTH_MODULE_PATH", "DEFAULT_REMINDER_ADVANCE_MINUTES",
    "HISTORY_FILE_PATH", "MEMORY_FILE_PATH", "LOG_LEVEL",
}


class Runtime:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.lock = threading.RLock()
        self.logs: deque[str] = deque(maxlen=300)
        self.reader: threading.Thread | None = None

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            with self.lock:
                self.logs.append(line.rstrip())
        with self.lock:
            if self.process is process:
                self.process = None

    def start(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            self.logs.append("Starting Omni-Assistant...")
            self.process = subprocess.Popen(
                [sys.executable, str(ROOT / "main.py")],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.reader = threading.Thread(
                target=self._read_output, args=(self.process,), daemon=True
            )
            self.reader.start()

    def stop(self) -> None:
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                self.process = None
                return
            self.logs.append("Stopping Omni-Assistant...")
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    process.terminate()
            else:
                process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        with self.lock:
            if self.process is process:
                self.process = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            process = self.process
            running = bool(process and process.poll() is None)
            return {
                "running": running,
                "pid": process.pid if running and process else None,
                "logs": list(self.logs)[-100:],
            }


runtime = Runtime()


def read_env(include_process: bool = True) -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if match:
                values[match.group(1)] = match.group(2).strip().strip("\"'")
    if include_process:
        for key in ALLOWED_KEYS:
            if key in os.environ:
                values[key] = os.environ[key]
    return values


def mask(value: str) -> str:
    return "" if not value else ("*" * max(8, min(24, len(value))))


def public_config() -> dict[str, str]:
    values = read_env()
    return {key: mask(value) if key in SENSITIVE_KEYS else value
            for key, value in values.items() if key in ALLOWED_KEYS}


def save_env(updates: dict[str, Any]) -> None:
    # Only merge the file when writing. Process-level variables may contain
    # injected secrets and must never be persisted by a dashboard save.
    current = read_env(include_process=False)
    for key, value in updates.items():
        if key not in ALLOWED_KEYS or not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"Unsupported configuration key: {key}")
        if key in SENSITIVE_KEYS and isinstance(value, str) and set(value) == {"*"}:
            continue
        current[key] = str(value).lower() if isinstance(value, bool) else str(value)
    lines = [
        "# Managed by Omni-Assistant dashboard. Review before production use.",
        *(f"{key}={current[key]}" for key in sorted(current) if key in ALLOWED_KEYS),
        "",
    ]
    temporary = ENV_FILE.with_suffix(".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, ENV_FILE)


class Handler(BaseHTTPRequestHandler):
    server_version = "OmniDashboard/1.0"

    def _authorized(self) -> bool:
        return not TOKEN or self.headers.get("X-Dashboard-Token") == TOKEN

    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type == "application/json" else payload
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "Unauthorized"})
            return
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, (WEB_ROOT / "index.html").read_bytes(), "text/html")
        elif path == "/api/config":
            self._send(200, public_config())
        elif path == "/api/status":
            self._send(200, runtime.status())
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "Unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/config":
                save_env(payload)
                self._send(200, {"ok": True, "restartRequired": True})
            elif path == "/api/service/start":
                runtime.start()
                self._send(200, runtime.status())
            elif path == "/api/service/stop":
                runtime.stop()
                self._send(200, runtime.status())
            elif path == "/api/service/restart":
                runtime.stop()
                runtime.start()
                self._send(200, runtime.status())
            else:
                self._send(404, {"error": "Not found"})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    WEB_ROOT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
