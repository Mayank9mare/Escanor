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

    if provider == "gemini":
        # Gemini speaks the OpenAI-compatible chat completions shape, so it
        # reuses OpenAIModel with Google's base_url. Verified live in
        # kmare-code: chat, streaming and tool calling.
        from .openai_provider import OpenAIModel
        api_key = config.get_api_key("gemini")
        if not api_key:
            raise RuntimeError("Set GEMINI_API_KEY (in your shell or .env) to use Gemini models.")
        return OpenAIModel(model_name=model_name, api_key=api_key, base_url=config.get_gemini_base_url())

    if provider == "ollama":
        from .ollama_provider import OllamaModel
        return OllamaModel(model_name=model_name, base_url=config.get_ollama_base_url())

    raise ValueError(f"Unknown provider: {provider}")
