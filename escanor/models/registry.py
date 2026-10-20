"""
models/registry.py

The factory that turns a provider + model id (usually from
config.resolve_model("sonnet")) into an actual Model object, without the
rest of the program needing to know how each provider is constructed.
Provider imports are lazy, so the `anthropic` / `openai` packages are only
needed when actually used.

Every model handed out is wrapped in InstrumentedModel (see
models/instrumented.py), so each LLM call is recorded in telemetry once,
here, rather than in every provider file.
"""
from __future__ import annotations
from .base import Model
from .instrumented import InstrumentedModel
from .. import config


def get_model(provider: str, model_name: str, role: str = "chat", effort: str | None = None) -> Model:
    return InstrumentedModel(_construct(provider, model_name, effort), provider=provider, role=role)


def _construct(provider: str, model_name: str, effort: str | None) -> Model:
    if provider == "mock":
        from .mock_provider import MockModel
        return MockModel(name=model_name)

    if provider == "anthropic":
        from .anthropic_provider import AnthropicModel
        api_key = config.get_api_key("anthropic")
        if not api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY (in your shell or .env) to use Claude models.")
        return AnthropicModel(model_name=model_name, api_key=api_key, effort=effort)

    if provider == "openai":
        from .openai_provider import OpenAIModel
        api_key = config.get_api_key("openai")
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY (in your shell or .env) to use OpenAI models.")
        return OpenAIModel(model_name=model_name, api_key=api_key)

    if provider in config.COMPATIBLE:
        # Gemini, Groq and OpenRouter speak the OpenAI-compatible chat
        # completions shape, so they reuse OpenAIModel with their own base_url.
        # Gemini verified live in kmare-code: chat, streaming and tool calling.
        from .openai_provider import OpenAIModel
        api_key = config.get_api_key(provider)
        if not api_key:
            env_var = config._ENV_VAR_BY_PROVIDER[provider]
            raise RuntimeError(f"Set {env_var} (in ~/.escanor/.env) to use {provider} models; "
                               f"get a free key at {config.COMPATIBLE[provider][1]}.")
        base_url = config.get_gemini_base_url() if provider == "gemini" else config.COMPATIBLE[provider][0]
        # Free tiers sometimes queue a request for half a minute: give up after
        # 20 s of silence and let failover try the next model (core/failover.py).
        # (15 s was too tight for Gemini on a busy day: ~10 s for a one-word reply.)
        return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url, timeout=20.0)

    if provider == "ollama":
        from .ollama_provider import OllamaModel
        return OllamaModel(model_name=model_name, base_url=config.get_ollama_base_url())

    raise ValueError(f"Unknown provider: {provider}")
