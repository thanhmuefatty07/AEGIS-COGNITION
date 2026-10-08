from core.python.aegis.provider_registry import (
    CUSTOM_PROVIDER_KIND,
    PROVIDER_SPECS,
    key_prefix_specs,
    provider_choices,
    provider_spec,
    provider_spec_for_endpoint,
    provider_spec_for_host,
)


def test_builtin_provider_registry_keeps_key_hints_and_native_catalogs_distinct():
    assert {spec.provider_kind for spec in PROVIDER_SPECS} >= {
        "anthropic",
        "google-gemini",
        "openai",
        "openrouter",
    }
    openai = provider_spec("OPENAI")
    assert openai is not None and openai.provider_kind == "openai"
    assert provider_spec("not-registered") is None
    assert provider_spec_for_endpoint("google-gemini", "generativelanguage.googleapis.com").catalog_strategy == "google"
    assert provider_spec_for_endpoint("google-gemini", "api.openai.com") is None
    assert provider_spec_for_endpoint("openai", "gateway.example") is None


def test_builtin_provider_kinds_and_native_hosts_are_unique():
    provider_kinds = [spec.provider_kind.casefold() for spec in PROVIDER_SPECS]
    native_hosts = [host.casefold() for spec in PROVIDER_SPECS for host in spec.native_hosts]

    assert len(provider_kinds) == len(set(provider_kinds))
    assert len(native_hosts) == len(set(native_hosts))


def test_key_prefixes_are_matched_most_specific_first():
    prefixes = key_prefix_specs()

    assert prefixes == tuple(sorted(prefixes, key=lambda entry: len(entry[0]), reverse=True))
    assert next(spec.provider_kind for prefix, spec in prefixes if prefix == "sk-or-") == "openrouter"
    assert next(spec.provider_kind for prefix, spec in prefixes if prefix == "sk-") == "openai"


def test_provider_specific_model_resource_only_applies_to_the_registered_host():
    xai = provider_spec_for_endpoint("xai", "api.x.ai")
    custom = provider_spec_for_endpoint("xai", "gateway.example")

    assert xai is not None and xai.models_resource == "language-models"
    assert custom is None


def test_provider_choices_come_from_the_registry_and_include_one_custom_endpoint_option():
    choices = provider_choices()
    built_in = [choice for choice in choices if choice["provider_kind"] != CUSTOM_PROVIDER_KIND]
    custom = choices[-1]

    assert {choice["provider_kind"] for choice in built_in} == {spec.provider_kind for spec in PROVIDER_SPECS}
    assert all(choice["endpoint"] == provider_spec(str(choice["provider_kind"])).endpoint for choice in built_in)
    assert custom == {
        "provider_kind": CUSTOM_PROVIDER_KIND,
        "provider_label": "Custom OpenAI-compatible",
        "endpoint": None,
        "protocol": "chat-completions",
        "requires_endpoint": True,
    }
    assert all("api_key" not in choice and "secret" not in choice for choice in choices)


def test_deepseek_requires_explicit_host_because_its_key_prefix_is_ambiguous():
    deepseek = provider_spec("deepseek")

    assert deepseek is not None
    assert deepseek.key_prefixes == ()
    assert provider_spec_for_host("API.DEEPSEEK.COM") is deepseek
    assert provider_spec_for_host("api.deepseek.com.attacker.example") is None
    assert provider_spec_for_endpoint("deepseek", "gateway.example") is None
