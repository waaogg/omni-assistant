import json


def test_dashboard_masks_secrets_and_preserves_masked_values(tmp_path, monkeypatch):
    import dashboard

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=secret-value\nLLM_MODEL=old-model\n", encoding="utf-8")
    monkeypatch.setattr(dashboard, "ENV_FILE", env_file)
    monkeypatch.setattr(dashboard, "ALLOWED_KEYS", {"LLM_API_KEY", "LLM_MODEL"})
    monkeypatch.setattr(dashboard, "SENSITIVE_KEYS", {"LLM_API_KEY"})
    monkeypatch.setenv("LLM_API_KEY", "injected-secret")

    public = dashboard.public_config()
    assert public["LLM_API_KEY"]
    assert set(public["LLM_API_KEY"]) == {"*"}
    assert public["LLM_MODEL"] == "old-model"

    dashboard.save_env({"LLM_API_KEY": "************", "LLM_MODEL": "new-model"})
    saved = env_file.read_text(encoding="utf-8")
    assert "LLM_API_KEY=secret-value" in saved
    assert "injected-secret" not in saved
    assert "LLM_MODEL=new-model" in saved


def test_dashboard_rejects_unknown_configuration_key(tmp_path, monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(dashboard, "ALLOWED_KEYS", {"LLM_MODEL"})
    try:
        dashboard.save_env({"NOT_ALLOWED": "value"})
    except ValueError as exc:
        assert "Unsupported configuration key" in str(exc)
    else:
        raise AssertionError("unknown dashboard key was accepted")


def test_dashboard_status_is_json_serializable():
    import dashboard

    payload = dashboard.runtime.status()
    json.dumps(payload)
