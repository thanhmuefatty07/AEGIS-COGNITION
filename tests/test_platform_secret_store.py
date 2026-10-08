import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pytest

from core.python.aegis import secrets as secret_module
from core.python.aegis.secrets import PlatformSecretStore, SecretStoreError


class FakeCredentialBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_platform_secret_store_persists_only_opaque_os_keyring_references():
    backend = FakeCredentialBackend()
    store = PlatformSecretStore(keyring_backend=backend)
    reference = "os-keyring:mcp-oauth:9f3e1d"

    store.store(reference, "refresh-token-value")

    assert store.resolve(reference) == "refresh-token-value"
    assert backend.values == {("aegis-cognition", "mcp-oauth:9f3e1d"): "refresh-token-value"}
    store.delete(reference)
    store.delete(reference)
    with pytest.raises(SecretStoreError, match="secret is unavailable"):
        store.resolve(reference)
    assert store.lookup(reference) is None


@pytest.mark.parametrize(
    "reference",
    (
        "session:temporary",
        "os-keyring:",
        "os-keyring: ../unsafe",
        "os-keyring:" + "x" * 256,
    ),
)
def test_platform_secret_store_rejects_invalid_persistent_secret_references(reference: str):
    store = PlatformSecretStore(keyring_backend=FakeCredentialBackend())

    with pytest.raises(SecretStoreError):
        store.store(reference, "sensitive-value")


def test_platform_secret_store_rejects_oversized_values_before_backend_write():
    backend = FakeCredentialBackend()
    store = PlatformSecretStore(keyring_backend=backend)

    with pytest.raises(SecretStoreError, match="secret value is invalid"):
        store.store("os-keyring:mcp-oauth:9f3e1d", "x" * (64 * 1024 + 1))

    assert backend.values == {}


def test_platform_secret_store_rejects_oversized_values_returned_by_backend():
    backend = FakeCredentialBackend()
    backend.values[("aegis-cognition", "mcp-oauth:9f3e1d")] = "x" * (64 * 1024 + 1)
    store = PlatformSecretStore(keyring_backend=backend)

    with pytest.raises(SecretStoreError, match="returned an invalid value"):
        store.lookup("os-keyring:mcp-oauth:9f3e1d")


@pytest.mark.parametrize("platform", ("win32", "darwin", "linux"))
def test_platform_secret_store_selects_only_the_native_backend(platform: str, monkeypatch: pytest.MonkeyPatch):
    backend = FakeCredentialBackend()
    backend_module = ModuleType(f"keyring.backends.{platform}")
    backend_type = type("NativeKeyring", (), {"__new__": lambda cls: backend})
    backend_name = {"win32": "Windows", "darwin": "macOS", "linux": "SecretService"}[platform]
    backend_module.__name__ = f"keyring.backends.{backend_name}"
    setattr(backend_module, "WinVaultKeyring" if platform == "win32" else "Keyring", backend_type)
    package = ModuleType("keyring")
    package.__path__ = []  # type: ignore[attr-defined]
    backends = ModuleType("keyring.backends")
    backends.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setattr(secret_module.sys, "platform", platform)
    monkeypatch.setitem(sys.modules, "keyring", package)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, backend_module.__name__, backend_module)

    assert secret_module._load_os_keyring_backend() is backend


def test_platform_secret_store_fails_closed_for_unsupported_platform(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(secret_module.sys, "platform", "emscripten")

    with pytest.raises(SecretStoreError, match="unsupported on this platform"):
        secret_module._load_os_keyring_backend()


def test_platform_secret_store_initializes_native_backend_once_under_concurrency(monkeypatch: pytest.MonkeyPatch):
    backend = FakeCredentialBackend()
    store = PlatformSecretStore()
    barrier = threading.Barrier(8)
    load_count = 0
    load_count_lock = threading.Lock()

    def load_backend() -> FakeCredentialBackend:
        nonlocal load_count
        with load_count_lock:
            load_count += 1
        return backend

    monkeypatch.setattr(secret_module, "_load_os_keyring_backend", load_backend)

    def get_backend() -> object:
        barrier.wait(timeout=2)
        return store._keyring_backend()

    with ThreadPoolExecutor(max_workers=8) as executor:
        loaded = tuple(executor.map(lambda _index: get_backend(), range(8)))

    assert load_count == 1
    assert all(item is backend for item in loaded)
