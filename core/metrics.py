"""Content-free AI latency and success metrics."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import config

_lock = threading.Lock()


def metric_path() -> Path:
    return Path(config.PROJECT_ROOT / "data" / "ai_metrics.ndjson")


def record_ai_metric(provider: str, model: str, duration_ms: int, success: bool, input_chars: int, output_chars: int = 0) -> None:
    item = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider,
        "model": model,
        "duration_ms": max(0, int(duration_ms)),
        "success": bool(success),
        "input_chars": max(0, int(input_chars)),
        "output_chars": max(0, int(output_chars)),
    }
    path = metric_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def summarize_metrics(limit: int = 1000) -> dict[str, Any]:
    path = metric_path()
    if not path.exists():
        return {"calls": 0, "failures": 0, "average_ms": 0}
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    items = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                items.append(value)
        except json.JSONDecodeError:
            continue
    total = sum(int(item.get("duration_ms", 0)) for item in items)
    return {
        "calls": len(items),
        "failures": sum(not item.get("success", False) for item in items),
        "average_ms": round(total / len(items)) if items else 0,
        "input_chars": sum(int(item.get("input_chars", 0)) for item in items),
        "output_chars": sum(int(item.get("output_chars", 0)) for item in items),
    }
