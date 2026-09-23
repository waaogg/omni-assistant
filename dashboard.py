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
import shutil
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
SENSITIVE_KEYS = {
    "LLM_API_KEY", "NAPCAT_TOKEN", "AGY_INSTALL_COMMAND",
    "ADMIN_QQ", "TARGET_GROUP_IDS", "ALLOWED_WECHAT_USER_IDS",
}
ALLOWED_KEYS = {
    "ENABLE_QQ", "ENABLE_WECHAT", "AI_PROVIDER", "AUTO_INSTALL_CLI",
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TEMPERATURE",
    "CODEX_BIN_PATH", "CODEX_ARGS", "OPENCODE_BIN_PATH", "OPENCODE_ARGS",
    "CLAUDE_BIN_PATH", "CLAUDE_ARGS", "AGY_BIN_PATH", "AGY_ARGS",
    "AGY_INSTALL_COMMAND", "PYTHON_BIN_PATH", "ADMIN_QQ", "TARGET_GROUP_IDS",
    "NAPCAT_HTTP_URL", "NAPCAT_WS_URL", "NAPCAT_TOKEN", "WECHAT_BASE_URL",
    "WECHAT_DATA_DIR", "ENABLE_MS_TODO", "MS_TODO_DEFAULT_LIST_ID",
    "MS_TODO_AUTH_MODULE_PATH", "DEFAULT_REMINDER_ADVANCE_MINUTES",
    "HISTORY_FILE_PATH", "MEMORY_FILE_PATH", "LOG_LEVEL",
    "DATABASE_FILE_PATH", "AUTO_APPLY_CONFIDENCE", "MESSAGE_BATCH_WINDOW_SECONDS",
    "MAX_MESSAGE_ATTEMPTS", "ALLOWED_WECHAT_USER_IDS",
    "REQUIRE_CONFIRMATION_FOR_DELETE", "DAILY_DIGEST_TIME",
    "WEEKLY_REVIEW_DAY", "QUIET_HOURS",
}

from core import config as app_config
from core.assistant_features import AssistantFeatures
from core.database import Database
from core.knowledge import KnowledgeService
from core.task_service import TaskService
from core.remote_todo import MicrosoftTodoRemote
from core.schedule_service import ScheduleService
from core.metrics import summarize_metrics

database = Database(app_config.DATABASE_FILE)
task_service = TaskService(database, MicrosoftTodoRemote() if app_config.ENABLE_MS_TODO else None)
assistant_features = AssistantFeatures(database, task_service)
knowledge_service = KnowledgeService(database)
schedule_service = ScheduleService(database)


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
            safe = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", line, flags=re.I)
            for key in SENSITIVE_KEYS:
                secret = os.getenv(key, "")
                if secret:
                    safe = safe.replace(secret, "[REDACTED]")
            with self.lock:
                self.logs.append(safe.rstrip())
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
        elif path == "/api/health":
            self._send(200, {
                "database": {"ok": True, **database.stats()},
                "python": {"ok": bool(sys.executable), "path": sys.executable},
                "node": {"ok": bool(shutil.which("node"))},
                "qq": {"enabled": app_config.ENABLE_QQ},
                "wechat": {"enabled": app_config.ENABLE_WECHAT},
                "todo": {"enabled": app_config.ENABLE_MS_TODO},
                "ai": {"provider": app_config.AI_PROVIDER, "model": app_config.LLM_MODEL},
            })
        elif path == "/api/tasks":
            self._send(200, task_service.db.list_tasks(None))
        elif path == "/api/inbox":
            self._send(200, database.list_inbox())
        elif path == "/api/preferences":
            self._send(200, assistant_features.get_preferences())
        elif path == "/api/digest":
            self._send(200, assistant_features.daily_digest())
        elif path == "/api/review":
            self._send(200, assistant_features.weekly_review())
        elif path == "/api/search":
            query = urlparse(self.path).query
            from urllib.parse import parse_qs
            terms = parse_qs(query).get("q", [""])[0]
            self._send(200, knowledge_service.search(terms))
        elif path == "/api/schedule":
            from datetime import date, timedelta
            today = date.today()
            self._send(200, schedule_service.occurrences(today, today + timedelta(days=14)))
        elif path == "/api/metrics":
            self._send(200, summarize_metrics())
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "Unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1024 * 1024:
                self._send(413, {"error": "Request body too large"})
                return
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
            elif path.startswith("/api/tasks/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    self._send(404, {"error": "Not found"})
                    return
                task_id, action = parts[2], parts[3]
                if action == "complete":
                    result = task_service.complete(task_id)
                elif action == "defer":
                    result = task_service.defer(task_id, int(payload.get("days", 1)))
                elif action == "waiting":
                    result = task_service.mark_waiting(task_id, str(payload.get("party", "")))
                elif action == "undo":
                    result = task_service.undo(task_id)
                else:
                    self._send(404, {"error": "Not found"})
                    return
                self._send(200 if result.get("success") else 400, result)
            elif path.startswith("/api/inbox/"):
                parts = path.strip("/").split("/")
                if len(parts) != 4 or parts[3] not in {"accept", "reject"}:
                    self._send(404, {"error": "Not found"})
                    return
                state = "accepted" if parts[3] == "accept" else "rejected"
                item = database.resolve_inbox(parts[2], state)
                if not item:
                    self._send(404, {"error": "Inbox item not found"})
                    return
                result = task_service.create(item["proposal"]) if state == "accepted" else {"success": True}
                self._send(200 if result.get("success") else 400, result)
            elif path == "/api/preferences":
                for key, value in payload.items():
                    assistant_features.set_preference(key, value)
                self._send(200, assistant_features.get_preferences())
            elif path == "/api/identity":
                channel = str(payload.get("channel", "")).strip()
                channel_user_id = str(payload.get("channelUserId", "")).strip()
                person_id = str(payload.get("personId", "primary")).strip()
                if not channel or not channel_user_id or not person_id:
                    raise ValueError("channel, channelUserId and personId are required")
                database.bind_identity(person_id, channel, channel_user_id)
                self._send(200, {"ok": True})
            elif path == "/api/schedule":
                self._send(200, schedule_service.add(payload))
            else:
                self._send(404, {"error": "Not found"})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    if HOST not in {"127.0.0.1", "localhost", "::1"} and not TOKEN:
        print("Refusing non-local dashboard binding without DASHBOARD_TOKEN", file=sys.stderr)
        return 2
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
