from pathlib import Path
from unittest.mock import patch

from core import config
from core.storage import load_json, save_json
from core.todo_helper import parse_iso_times


def test_atomic_json_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    payload = {"message": "中文", "items": [1, 2, 3]}

    save_json(path, payload)

    assert load_json(path, {}) == payload
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_json_returns_default(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")

    assert load_json(path, {"fallback": True}) == {"fallback": True}


def test_parse_iso_times_preserves_explicit_reminder():
    original = config.DEFAULT_REMINDER_ADVANCE_MINUTES
    try:
        config.DEFAULT_REMINDER_ADVANCE_MINUTES = 15
        due, reminder = parse_iso_times("2026年9月15日 14:00 - 16:00，提前30分钟")
    finally:
        config.DEFAULT_REMINDER_ADVANCE_MINUTES = original

    assert due == "2026-09-15T14:00:00"
    assert reminder == "2026-09-15T13:30:00"


def test_parse_iso_times_rejects_invalid_dates():
    assert parse_iso_times("2026年2月30日 10:00") == (None, None)


def test_validate_config_reports_enabled_qq_requirements():
    with patch.object(config, "ENABLE_QQ", True), \
         patch.object(config, "ADMIN_QQ", 0), \
         patch.object(config, "TARGET_GROUP_IDS", []):
        errors = config.validate_config()

    assert "ADMIN_QQ must be a positive integer when ENABLE_QQ=true" in errors
    assert "TARGET_GROUP_IDS must contain at least one group when ENABLE_QQ=true" in errors
