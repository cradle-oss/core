"""Focused tests for model provider selection and configuration precedence."""

import pytest

from core.model.provider import (
    OPENAI_DEFAULT_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_DEFAULT_MODEL,
    OpenAIProvider,
    ProviderConfigError,
    resolve_provider_config,
)


def test_no_credentials_fails_with_setup(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError) as exc:
        resolve_provider_config()
    assert "OPENROUTER_API_KEY" in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)


def test_openrouter_key_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    config = resolve_provider_config()
    assert config.provider == "openrouter"
    assert config.requested_model == OPENROUTER_DEFAULT_MODEL
    assert config.base_url == OPENROUTER_BASE_URL
    assert config.api_key == "or-key"


def test_openai_default_is_current_flagship(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    config = resolve_provider_config()
    assert OPENAI_DEFAULT_MODEL == "gpt-6-astra"
    assert config.requested_model == "gpt-6-astra"


def test_stale_defaults_not_used(monkeypatch):
    """Stale model names must not appear in the OpenAI default."""
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    config = resolve_provider_config()
    assert config.requested_model not in {"gpt-4", "gpt-4o", "gpt-5", "gpt-5.6"}


def test_openai_key_defaults_to_current_default(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    config = resolve_provider_config()
    assert config.provider == "openai"
    assert config.requested_model == OPENAI_DEFAULT_MODEL
    assert config.base_url is None
    assert config.api_key == "oa-key"


def test_openrouter_preferred_over_openai_when_both(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    config = resolve_provider_config()
    assert config.provider == "openrouter"
    assert config.requested_model == OPENROUTER_DEFAULT_MODEL


def test_explicit_model_flag_uses_matching_provider(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    config = resolve_provider_config(model_arg="openrouter/anthropic/claude-3.5-sonnet")
    assert config.provider == "openrouter"
    assert config.requested_model == "openrouter/anthropic/claude-3.5-sonnet"
    assert config.api_key == "or-key"


def test_explicit_non_openrouter_model_ignores_or_key_only(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    with pytest.raises(ProviderConfigError) as exc:
        resolve_provider_config(model_arg="gpt-test-model")
    assert "OPENAI_API_KEY" in str(exc.value)


def test_explicit_openrouter_model_requires_or_key(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    with pytest.raises(ProviderConfigError) as exc:
        resolve_provider_config(model_arg="openrouter/auto")
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_explicit_openai_model_requires_oa_key(monkeypatch):
    monkeypatch.delenv("CORE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    with pytest.raises(ProviderConfigError) as exc:
        resolve_provider_config(model_arg="gpt-test-model")
    assert "OPENAI_API_KEY" in str(exc.value)


def test_core_model_env_used_as_explicit(monkeypatch):
    monkeypatch.setenv("CORE_MODEL", "openrouter/auto")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    config = resolve_provider_config()
    assert config.provider == "openrouter"
    assert config.requested_model == "openrouter/auto"


def test_model_arg_overrides_core_model_env(monkeypatch):
    monkeypatch.setenv("CORE_MODEL", "gpt-test-model")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    config = resolve_provider_config(model_arg="gpt-test-model")
    assert config.requested_model == "gpt-test-model"


def test_config_repr_hides_key():
    from core.model.provider import ProviderConfig

    config = ProviderConfig(
        provider="openai",
        base_url=None,
        api_key="super-secret",
        requested_model="gpt-test-model",
    )
    rendered = repr(config)
    assert "super-secret" not in rendered
    assert "api_key='...'" in rendered


def test_openai_provider_constructs_with_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    provider = OpenAIProvider(
        model=OPENROUTER_DEFAULT_MODEL,
        api_key="sk-test-not-real",
        base_url=OPENROUTER_BASE_URL,
        provider="openrouter",
    )
    assert provider.provider == "openrouter"
    assert provider.base_url == OPENROUTER_BASE_URL
