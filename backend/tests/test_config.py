from app.config import Settings


def test_settings_loads_env_local_after_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLM_PROVIDER=openai\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("LLM_PROVIDER=mock\n", encoding="utf-8")

    assert Settings().llm_provider == "mock"
