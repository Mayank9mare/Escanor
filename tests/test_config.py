import os
import pytest
from escanor import config


def test_should_resolve_alias_to_provider_and_model_when_alias_known():
    settings = config.load_settings()
    assert config.resolve_model("sonnet", settings) == ("anthropic", "claude-sonnet-5-5")


def test_should_keep_ollama_tag_when_spec_has_second_colon():
    settings = config.load_settings()
    assert config.resolve_model("ollama:qwen3:8b", settings) == ("ollama", "qwen3:8b")


@pytest.mark.parametrize("name", ["bogus", "nope:model", "anthropic:", ""])
def test_should_raise_value_error_when_model_unknown(name):
    with pytest.raises(ValueError, match="Unknown model"):
        config.resolve_model(name, config.load_settings())


def test_should_merge_user_toml_over_defaults_when_file_exists(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[models]\ndefault = "sonnet"\n[models.aliases]\nfast = "gemini:gemini-3.6-flash"\n')
    settings = config.load_settings(path)
    assert settings["models"]["default"] == "sonnet"
    assert settings["models"]["aliases"]["fast"] == "gemini:gemini-3.6-flash"
    assert settings["models"]["aliases"]["haiku"] == "anthropic:claude-haiku-5-5"  # defaults kept
    assert settings["agent"]["max_steps"] == 8


def test_should_raise_when_toml_is_broken(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[models\n")
    with pytest.raises(RuntimeError, match="Could not parse"):
        config.load_settings(path)


def test_should_not_mutate_defaults_when_settings_modified():
    settings = config.load_settings()
    settings["models"]["aliases"]["haiku"] = "changed"
    assert config.DEFAULTS["models"]["aliases"]["haiku"] == "anthropic:claude-haiku-5-5"


def test_should_load_dotenv_without_overriding_exported_vars(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nGEMINI_API_KEY='from-file'\nOPENAI_API_KEY=from-file\nEMPTY=\n")
    monkeypatch.setenv("OPENAI_API_KEY", "exported")
    config.load_dotenv(env)
    assert os.environ["GEMINI_API_KEY"] == "from-file"
    assert os.environ["OPENAI_API_KEY"] == "exported"
    assert "EMPTY" not in os.environ
