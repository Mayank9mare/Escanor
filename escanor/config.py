"""
config.py

Where Escanor's settings come from:

- API keys: environment variables only (ANTHROPIC_API_KEY, ...), so a
  key is never written into code or the config file. `.env` in the
  project root is loaded too, if present (see load_dotenv()).
- Everything else: ~/.escanor/config.toml (ESCANOR_CONFIG to override),
  merged over the DEFAULTS below. The file is optional.

Model aliases
-------------
A model is named "provider:model-id" (e.g. "anthropic:claude-haiku-5-5",
"ollama:qwen3:8b") or by an alias from [models.aliases]. resolve_model()
turns either into (provider, model_id). Only the first ":" splits, so
Ollama tags like "qwen3:8b" survive.
"""
from __future__ import annotations
import copy
import os
import tomllib
from pathlib import Path
from typing import Any

_ENV_VAR_BY_PROVIDER = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "sarvam": "SARVAM_API_KEY",
}

PROVIDERS = ("anthropic", "openai", "gemini", "ollama", "mock")

DEFAULTS: dict[str, Any] = {
    "models": {
        "default": "haiku",
        # Claude effort: low | medium | high | xhigh | max. "low" keeps a
        # voice assistant snappy; raise it (or switch to sonnet) for hard work.
        "effort": "low",
        "aliases": {
            "haiku": "anthropic:claude-haiku-5-5",
            "sonnet": "anthropic:claude-sonnet-5-5",
            "gpt": "openai:gpt-5",
            "gemini": "gemini:gemini-3.6-flash",
            "local": "ollama:qwen3:8b",
            "mock": "mock:mock-model",
        },
    },
    "agent": {
        "max_steps": 8,
    },
}


def data_dir() -> Path:
    """Escanor's local data: conversations, telemetry, later the brain."""
    return Path(os.environ.get("ESCANOR_HOME", str(Path.home() / ".escanor")))


def config_path() -> Path:
    return Path(os.environ.get("ESCANOR_CONFIG", str(data_dir() / "config.toml")))


def _merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """DEFAULTS merged with the TOML file, if there is one. A broken file
    is an error rather than a silent fallback to defaults, so a typo
    doesn't quietly change which model you're paying for."""
    path = path or config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        with path.open("rb") as f:
            user = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"Could not parse {path}: {e}") from e
    return _merge(DEFAULTS, user)


def resolve_model(name: str, settings: dict[str, Any]) -> tuple[str, str]:
    """'sonnet' -> ('anthropic', 'claude-sonnet-5-5'); 'ollama:llama3.2' ->
    ('ollama', 'llama3.2'). Raises ValueError for anything else."""
    name = name.strip()
    aliases = settings["models"]["aliases"]
    spec = aliases.get(name, name)
    provider, sep, model_id = spec.partition(":")
    if not sep or not model_id or provider not in PROVIDERS:
        known = ", ".join(sorted(aliases))
        raise ValueError(
            f"Unknown model '{name}'. Use an alias ({known}) or provider:model-id "
            f"with provider one of {', '.join(PROVIDERS)}."
        )
    return provider, model_id


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=value lines, # comments). Existing
    environment variables win, so an exported key overrides the file."""
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def get_api_key(provider: str) -> str | None:
    env_var = _ENV_VAR_BY_PROVIDER.get(provider)
    return os.environ.get(env_var) if env_var else None


def get_ollama_base_url() -> str:
    """Ollama's HTTP API (no key needed). Defaults to localhost."""
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def get_gemini_base_url() -> str:
    """Google's OpenAI-compatible endpoint for Gemini. Get a key at
    https://aistudio.google.com/apikey. The free tier allows Google to use
    prompts to improve its products -- don't send it anything confidential."""
    return os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
