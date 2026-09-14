"""Conservative language/framework adapter registry.

Detection is useful immediately.  An adapter is not promoted beyond its
conformance level until fixtures provide evidence for that level.
"""

from __future__ import annotations

from dataclasses import dataclass

from .generic import AdapterCapabilities, GenericBoundedAdapter


@dataclass(frozen=True)
class AdapterDescriptor:
    name: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    level: str
    status: str


_DESCRIPTORS = (
    AdapterDescriptor("python-pytest", ("python",), ("pytest",), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("rust-cargo", ("rust",), ("cargo",), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("javascript-node", ("javascript",), ("node",), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("typescript-node", ("typescript",), ("node",), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("go-test", ("go",), ("go-test",), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("dotnet-test", ("dotnet",), (), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("jvm-junit", ("jvm",), ("junit-maven", "junit-gradle"), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("cpp-ctest", ("cpp",), ("ctest",), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("ruby-test", ("ruby",), (), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("php-test", ("php",), (), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("swift-test", ("swift",), (), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("dart-test", ("dart",), (), "L0", "DETECTED_ONLY"),
    AdapterDescriptor("custom", (), (), "L1", "BOUNDED_COMMAND_ONLY"),
)


class AdapterRegistry:
    """Select descriptors without executing discovery or test commands."""

    def __init__(self) -> None:
        self._adapters = {descriptor.name: GenericBoundedAdapter() for descriptor in _DESCRIPTORS}

    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        return _DESCRIPTORS

    def detect(self, *, languages: tuple[str, ...], frameworks: tuple[str, ...]) -> tuple[AdapterDescriptor, ...]:
        language_set = set(languages)
        framework_set = set(frameworks)
        matches = tuple(
            descriptor
            for descriptor in _DESCRIPTORS
            if descriptor.name != "custom"
            and language_set.intersection(descriptor.languages)
            and (not descriptor.frameworks or framework_set.intersection(descriptor.frameworks))
        )
        return matches or (next(descriptor for descriptor in _DESCRIPTORS if descriptor.name == "custom"),)

    def adapter(self, descriptor: AdapterDescriptor) -> GenericBoundedAdapter:
        if descriptor.name not in self._adapters:
            raise KeyError("adapter is not registered")
        return self._adapters[descriptor.name]

    def capabilities(self, descriptor: AdapterDescriptor) -> AdapterCapabilities:
        base = self.adapter(descriptor).capabilities()
        return AdapterCapabilities(
            adapter=descriptor.name,
            level=descriptor.level,
            structured_discovery=base.structured_discovery and descriptor.level in {"L2", "L3"},
            structured_results=base.structured_results,
            impact_aware_planning=descriptor.level == "L3",
            quality_assessment=descriptor.level == "L3",
        )


__all__ = ["AdapterDescriptor", "AdapterRegistry"]
