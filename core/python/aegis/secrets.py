"""Platform-bound secret reference resolution and OS-vault storage.

Persistent secret values stay in an explicitly selected native OS credential
store. Callers persist only an opaque ``os-keyring:`` reference.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import sys
import threading
from collections.abc import Mapping
from typing import Any, ClassVar, Protocol


_OS_KEYRING_SERVICE = "aegis-cognition"
_MAX_OS_KEYRING_ACCOUNT_CHARS = 255
_MAX_SECRET_CHARS = 64 * 1024


class _CredentialBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class SecretStoreError(RuntimeError):
    """A secret reference cannot be resolved safely."""


class PlatformSecretStore:
    """Resolve only explicitly scoped references using the current platform."""

    def __init__(
        self,
        *,
        session_secrets: Mapping[str, str] | None = None,
        environ: Mapping[str, str] | None = None,
        keyring_backend: _CredentialBackend | None = None,
    ) -> None:
        self._session_secrets = dict(session_secrets or {})
        self._environ = environ if environ is not None else os.environ
        self._credential_backend = keyring_backend
        self._credential_backend_lock = threading.Lock()

    def resolve(self, secret_ref: str) -> str:
        if not isinstance(secret_ref, str) or not secret_ref.strip():
            raise SecretStoreError("secret reference must be non-empty")
        prefix, separator, value = secret_ref.partition(":")
        if not separator or not value or value != value.strip():
            raise SecretStoreError("secret reference is malformed")
        if prefix == "env":
            secret = self._environ.get(value)
        elif prefix == "session":
            secret = self._session_secrets.get(value)
        elif prefix == "windows-credential":
            secret = self._resolve_windows_credential(value)
        elif prefix == "os-keyring":
            secret = self.lookup(secret_ref)
        elif prefix in {"keychain", "secret-service"}:
            raise SecretStoreError(f"secret backend is unavailable: {prefix}")
        else:
            raise SecretStoreError("secret reference scheme is unsupported")
        if not isinstance(secret, str) or not secret:
            raise SecretStoreError("secret is unavailable")
        return secret

    def lookup(self, secret_ref: str) -> str | None:
        """Read an optional OS-vault value without conflating absence and backend failure."""

        account = self._parse_keyring_reference(secret_ref)
        try:
            secret = self._keyring_backend().get_password(_OS_KEYRING_SERVICE, account)
        except Exception:
            raise SecretStoreError("OS secure credential store could not be read") from None
        if secret is not None and (type(secret) is not str or not secret or len(secret) > _MAX_SECRET_CHARS):
            raise SecretStoreError("OS secure credential store returned an invalid value")
        return secret

    def store(self, secret_ref: str, secret: str) -> None:
        """Persist a secret under an opaque OS-keyring reference."""

        account = self._parse_keyring_reference(secret_ref)
        if type(secret) is not str or not secret or len(secret) > _MAX_SECRET_CHARS:
            raise SecretStoreError("secret value is invalid")
        try:
            self._keyring_backend().set_password(_OS_KEYRING_SERVICE, account, secret)
        except Exception:
            raise SecretStoreError("OS secure credential store could not be written") from None

    def delete(self, secret_ref: str) -> None:
        """Delete an OS-keyring entry; deleting an absent entry is harmless."""

        account = self._parse_keyring_reference(secret_ref)
        try:
            backend = self._keyring_backend()
            if backend.get_password(_OS_KEYRING_SERVICE, account) is not None:
                backend.delete_password(_OS_KEYRING_SERVICE, account)
        except Exception:
            raise SecretStoreError("OS secure credential store could not be updated") from None

    @staticmethod
    def _validate_keyring_account(account: str) -> str:
        if (
            type(account) is not str
            or not account
            or len(account) > _MAX_OS_KEYRING_ACCOUNT_CHARS
            or account != account.strip()
            or any(not (char.isascii() and (char.isalnum() or char in "._:-")) for char in account)
        ):
            raise SecretStoreError("OS keyring reference is malformed")
        return account

    @classmethod
    def _parse_keyring_reference(cls, secret_ref: str) -> str:
        if type(secret_ref) is not str:
            raise SecretStoreError("secret reference must be non-empty")
        prefix, separator, account = secret_ref.partition(":")
        if not separator or prefix != "os-keyring":
            raise SecretStoreError("persistent secrets require an os-keyring reference")
        return cls._validate_keyring_account(account)

    def _keyring_backend(self) -> _CredentialBackend:
        backend = self._credential_backend
        if backend is None:
            with self._credential_backend_lock:
                backend = self._credential_backend
                if backend is None:
                    backend = _load_os_keyring_backend()
                    self._credential_backend = backend
        return backend

    @staticmethod
    def _resolve_windows_credential(target: str) -> str:
        if sys.platform != "win32":
            raise SecretStoreError("Windows Credential Manager is unavailable on this platform")
        if not target or target != target.strip():
            raise SecretStoreError("Windows credential target is malformed")
        return _read_windows_generic_credential(target)


if sys.platform == "win32":

    class _Credential(ctypes.Structure):
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


def _read_windows_generic_credential(target: str) -> str:
    if sys.platform != "win32":
        raise SecretStoreError("Windows Credential Manager is unavailable on this platform")
    advapi32: Any = ctypes.WinDLL("advapi32", use_last_error=True)
    credential = ctypes.POINTER(_Credential)()
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    if not advapi32.CredReadW(target, 1, 0, ctypes.byref(credential)):
        raise SecretStoreError("Windows credential could not be read")
    try:
        record = credential.contents
        size = int(record.CredentialBlobSize)
        if size <= 0 or not record.CredentialBlob:
            raise SecretStoreError("Windows credential is empty")
        raw = ctypes.string_at(record.CredentialBlob, size)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStoreError("Windows credential is not UTF-8 text") from exc
    finally:
        advapi32.CredFree(credential)


def _load_os_keyring_backend() -> _CredentialBackend:
    """Load only the native backend for this OS; never accept file/null fallbacks."""

    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            return WinVaultKeyring()
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            return Keyring()
        if sys.platform.startswith("linux"):
            from keyring.backends.SecretService import Keyring

            return Keyring()
    except Exception:
        raise SecretStoreError("native OS secure credential store is unavailable") from None
    raise SecretStoreError("native OS secure credential store is unsupported on this platform")


__all__ = ["PlatformSecretStore", "SecretStoreError"]
