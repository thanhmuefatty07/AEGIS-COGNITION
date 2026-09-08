"""Platform-bound secret reference resolution.

The catalog stores only an opaque reference. This module resolves a reference
at request time and keeps the returned value in process memory only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import sys
from collections.abc import Mapping
from typing import Any, ClassVar


class SecretStoreError(RuntimeError):
    """A secret reference cannot be resolved safely."""


class PlatformSecretStore:
    """Resolve only explicitly scoped references using the current platform."""

    def __init__(
        self,
        *,
        session_secrets: Mapping[str, str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._session_secrets = dict(session_secrets or {})
        self._environ = environ if environ is not None else os.environ

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
        elif prefix in {"keychain", "secret-service"}:
            raise SecretStoreError(f"secret backend is unavailable: {prefix}")
        else:
            raise SecretStoreError("secret reference scheme is unsupported")
        if not isinstance(secret, str) or not secret:
            raise SecretStoreError("secret is unavailable")
        return secret

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
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
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


__all__ = ["PlatformSecretStore", "SecretStoreError"]
