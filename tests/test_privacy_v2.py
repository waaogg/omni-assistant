from pathlib import Path
from io import BytesIO
from urllib.error import HTTPError

import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_no_production_state_is_tracked():
    forbidden_names = {
        "auth.json", "token-cache.json", "sync_buf.txt", "conversations.json",
        "group_history.json", "synced_todos.json", "course_schedule.json",
        "qrcode.png", "received_image.png",
    }
    tracked = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
    }
    assert not {path for path in tracked if Path(path).name in forbidden_names}


def test_no_unsafe_agent_permission_bypass_or_production_ids():
    needles = (
        "dangerously-" + "skip-permissions",
        "AQMk" + "ADAw",
        "ilink_user_" + "id\":\"",
    )
    findings = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in {".py", ".js", ".json", ".md", ".yml", ".yaml", ".example"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in needles:
            if needle in text:
                findings.append(f"{path.relative_to(ROOT)}: {needle}")
    assert findings == []


def test_environment_template_uses_only_neutral_placeholders():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "sk-your-" in text
    assert "10000001" in text
    assert "90000001" in text
    assert "your_napcat_access_token_here" in text


def test_provider_error_body_is_not_logged_or_returned(monkeypatch, caplog, tmp_path):
    from core import ai_provider, metrics

    private_echo = "private-message-that-must-not-escape"
    error = HTTPError(
        "https://provider.invalid/chat/completions",
        400,
        "bad request",
        {},
        BytesIO(private_echo.encode("utf-8")),
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(ai_provider.urllib.request, "urlopen", fail)
    monkeypatch.setattr(metrics, "metric_path", lambda: tmp_path / "metrics.ndjson")
    with pytest.raises(ai_provider.AIProviderError) as caught:
        ai_provider._call_openai_compatible([{"role": "user", "content": private_echo}])

    assert private_echo not in str(caught.value)
    assert private_echo not in caplog.text
    metric_text = (tmp_path / "metrics.ndjson").read_text(encoding="utf-8")
    assert private_echo not in metric_text


def test_runtime_logs_do_not_emit_message_content_or_configured_endpoints():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "main.py",
            ROOT / "core" / "todo_manager.py",
            ROOT / "adapters" / "qq" / "qqbot_agent.py",
            ROOT / "adapters" / "wechat" / "wechat_bot.js",
        )
    )
    forbidden_log_fragments = (
        "参数: {args}",
        "非标准 JSON: {raw_res}",
        "Base URL: {config.LLM_BASE_URL}",
        "NapCat API: {config.NAPCAT_HTTP_URL}",
        "AI 执行失败: ${execErr.message}",
        "errmsg=${updates.errmsg",
    )
    assert not [fragment for fragment in forbidden_log_fragments if fragment in sources]
