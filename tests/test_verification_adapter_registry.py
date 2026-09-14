from aegis_cognition.verification.adapters import AdapterRegistry


def test_supported_language_inventory_is_detected_without_overclaiming_conformance() -> None:
    registry = AdapterRegistry()
    descriptors = registry.detect(languages=("python", "rust"), frameworks=("pytest", "cargo"))
    assert {descriptor.name for descriptor in descriptors} == {"python-pytest", "rust-cargo"}
    assert all(descriptor.level == "L0" for descriptor in descriptors)
    assert all(descriptor.status == "DETECTED_ONLY" for descriptor in descriptors)


def test_unknown_project_falls_back_to_custom_bounded_adapter() -> None:
    registry = AdapterRegistry()
    descriptors = registry.detect(languages=(), frameworks=())
    assert descriptors[0].name == "custom"
    assert registry.capabilities(descriptors[0]).level == "L1"
