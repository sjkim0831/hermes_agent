"""Tests for Groq, Fireworks AI, and Cerebras provider support."""

import pytest

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
    resolve_provider,
)


@pytest.mark.parametrize(
    ("provider_id", "name", "base_url", "env_var", "base_url_env_var"),
    [
        ("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_BASE_URL"),
        ("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "FIREWORKS_BASE_URL"),
        ("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "CEREBRAS_BASE_URL"),
    ],
)
def test_provider_registry_entries(provider_id, name, base_url, env_var, base_url_env_var):
    pconfig = PROVIDER_REGISTRY[provider_id]
    assert pconfig.name == name
    assert pconfig.auth_type == "api_key"
    assert pconfig.inference_base_url == base_url
    assert pconfig.api_key_env_vars == (env_var,)
    assert pconfig.base_url_env_var == base_url_env_var


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("groq", "groq"),
        ("groq-cloud", "groq"),
        ("fireworks", "fireworks"),
        ("fireworks-ai", "fireworks"),
        ("cerebras", "cerebras"),
        ("cerebras-ai", "cerebras"),
    ],
)
def test_provider_aliases(alias, expected, monkeypatch):
    monkeypatch.setenv(PROVIDER_REGISTRY[expected].api_key_env_vars[0], "sk-test")
    assert resolve_provider(alias) == expected

    from hermes_cli.models import normalize_provider as normalize_models_provider
    from hermes_cli.providers import normalize_provider as normalize_runtime_provider

    assert normalize_models_provider(alias) == expected
    assert normalize_runtime_provider(alias) == expected


@pytest.mark.parametrize("provider_id", ["groq", "fireworks", "cerebras"])
def test_canonical_provider_list_contains_new_providers(provider_id):
    from hermes_cli.models import CANONICAL_PROVIDERS

    slugs = [entry.slug for entry in CANONICAL_PROVIDERS]
    assert provider_id in slugs


@pytest.mark.parametrize(
    ("provider_id", "env_var", "base_url_env_var", "override_url"),
    [
        ("groq", "GROQ_API_KEY", "GROQ_BASE_URL", "https://groq.example/v1"),
        ("fireworks", "FIREWORKS_API_KEY", "FIREWORKS_BASE_URL", "https://fireworks.example/v1"),
        ("cerebras", "CEREBRAS_API_KEY", "CEREBRAS_BASE_URL", "https://cerebras.example/v1"),
    ],
)
def test_api_key_status_and_override(provider_id, env_var, base_url_env_var, override_url, monkeypatch):
    monkeypatch.setenv(env_var, "sk-test-123")
    status = get_api_key_provider_status(provider_id)
    assert status["configured"]

    monkeypatch.setenv(base_url_env_var, override_url)
    creds = resolve_api_key_provider_credentials(provider_id)
    assert creds["api_key"] == "sk-test-123"
    assert creds["base_url"] == override_url


def test_models_dev_and_url_inference_mappings():
    from agent.model_metadata import _infer_provider_from_url
    from agent.models_dev import PROVIDER_TO_MODELS_DEV

    assert PROVIDER_TO_MODELS_DEV["groq"] == "groq"
    assert PROVIDER_TO_MODELS_DEV["fireworks"] == "fireworks-ai"
    assert PROVIDER_TO_MODELS_DEV["cerebras"] == "cerebras"

    assert _infer_provider_from_url("https://api.groq.com/openai/v1") == "groq"
    assert _infer_provider_from_url("https://api.fireworks.ai/inference/v1") == "fireworks"
    assert _infer_provider_from_url("https://api.cerebras.ai/v1") == "cerebras"


def test_static_model_fallbacks_exist():
    from hermes_cli.models import _PROVIDER_MODELS

    assert _PROVIDER_MODELS["groq"] == ["openai/gpt-oss-120b"]
    assert _PROVIDER_MODELS["fireworks"] == ["fireworks/minimax-m2p5"]
    assert _PROVIDER_MODELS["cerebras"] == ["llama3.1-8b", "qwen-3-235b-a22b-instruct-2507"]
