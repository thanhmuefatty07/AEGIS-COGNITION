"""Bounded local capability registry for tools, extensions, MCP, and skills.

This module is intentionally an adapter layer.  It does not replace the Lab
ledger or the Rust authority: tool calls still enter the existing generic tool
cell, while this registry owns discovery, lazy metadata, and handler lookup.
External code is discoverable without being executed.  Activation is explicit
and every registered capability declares its effect class.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import http.client
import ipaddress
import inspect
import json
import math
import re
import socket
import ssl
import threading
import tomllib
from urllib.parse import SplitResult, urlsplit
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast


EXTENSION_MANIFEST_SCHEMA_V1 = "aegis-extension-manifest-v1"
TOOL_DESCRIPTOR_SCHEMA_V1 = "aegis-tool-descriptor-v1"
SKILL_DESCRIPTOR_SCHEMA_V1 = "aegis-skill-descriptor-v1"
MCP_SERVER_SCHEMA_V1 = "aegis-mcp-server-v1"

_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_EFFECT_CLASSES = frozenset(
    {
        "read_only",
        "network_read",
        "compute",
        "model_inference",
        "local_reversible",
        "state_write",
        "external_write",
        "destructive",
    }
)
_CONCURRENCY_MODES = frozenset({"parallel", "serial"})
_SKIP_DIRECTORIES = frozenset({".git", ".venv", "node_modules", "target", "dist", "build", "__pycache__"})
_MAX_SCHEMA_BYTES = 256 * 1024
_MAX_PROMPT_WORDS = 8_192


class ExtensionError(ValueError):
    """Raised when an extension boundary is invalid or not activated."""


class ExtensionNotFound(ExtensionError):
    """Raised when a requested capability is not registered."""


class ToolExecutionError(ExtensionError):
    """Raised when a registered tool cannot produce a bounded JSON result."""


def _bounded_name(value: object, label: str) -> str:
    if type(value) is not str:
        raise ExtensionError(f"{label} must be a string")
    normalized = value.strip().lower()
    if not _NAME_PATTERN.fullmatch(normalized):
        raise ExtensionError(f"{label} must use lowercase letters, digits, '.', '_' or '-'")
    return normalized


def _bounded_text(value: object, label: str, limit: int = 4_096) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value or len(value) > limit:
        raise ExtensionError(f"{label} must be bounded non-empty text")
    return value.strip()


def _text_tuple(value: object, label: str, *, limit: int = 32) -> tuple[str, ...]:
    if type(value) not in (list, tuple, set, frozenset):
        raise ExtensionError(f"{label} must be a sequence of strings")
    items = tuple(_bounded_text(item, f"{label} item", 256) for item in cast(Sequence[object], value))
    if len(items) > limit or len(set(items)) != len(items):
        raise ExtensionError(f"{label} contains too many or duplicate items")
    return tuple(sorted(items))


def _json_value(value: object, *, label: str, max_bytes: int = _MAX_SCHEMA_BYTES) -> object:
    """Validate JSON-shaped data and return a detached canonical copy."""

    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[object, object], value)
        if any(type(key) is not str for key in mapping_value):
            raise ExtensionError(f"{label} object keys must be strings")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > max_bytes:
            raise ExtensionError(f"{label} exceeds its byte budget")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ExtensionError(f"{label} must be JSON-compatible") from error
    return decoded


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Model-facing metadata for one registered tool."""

    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}))
    effect_class: str = "read_only"
    capabilities: tuple[str, ...] = ("read_only",)
    timeout_seconds: float = 30.0
    concurrency: str = "parallel"
    max_result_bytes: int = 256 * 1024
    extension_id: str = "core"

    def validate(self) -> None:
        _bounded_name(self.name, "tool name")
        _bounded_text(self.description, "tool description", 8_192)
        if self.effect_class not in _EFFECT_CLASSES:
            raise ExtensionError(f"unsupported tool effect class: {self.effect_class}")
        if self.concurrency not in _CONCURRENCY_MODES:
            raise ExtensionError("tool concurrency must be parallel or serial")
        if (
            type(self.timeout_seconds) not in (int, float)
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 3_600
        ):
            raise ExtensionError("tool timeout must be within (0, 3600] seconds")
        if type(self.max_result_bytes) is not int or not 1 <= self.max_result_bytes <= 16 * 1024 * 1024:
            raise ExtensionError("tool result byte limit is invalid")
        _text_tuple(self.capabilities, "tool capabilities")
        _bounded_name(self.extension_id, "tool extension id")
        schema = _json_value(self.input_schema, label="tool input schema")
        if not isinstance(schema, dict):
            raise ExtensionError("tool input schema must be a JSON object")

    @property
    def descriptor_hash(self) -> str:
        self.validate()
        return _sha256_json(
            {
                "schema": TOOL_DESCRIPTOR_SCHEMA_V1,
                "name": self.name,
                "description": self.description,
                "input_schema": self.input_schema,
                "effect_class": self.effect_class,
                "capabilities": self.capabilities,
                "timeout_seconds": self.timeout_seconds,
                "concurrency": self.concurrency,
                "max_result_bytes": self.max_result_bytes,
                "extension_id": self.extension_id,
            }
        )

    def summary(self) -> dict[str, object]:
        self.validate()
        return {
            "schema": TOOL_DESCRIPTOR_SCHEMA_V1,
            "name": self.name,
            "description": self.description,
            "effect_class": self.effect_class,
            "capabilities": list(self.capabilities),
            "extension_id": self.extension_id,
            "descriptor_hash": self.descriptor_hash,
        }


ToolHandler = Callable[[Mapping[str, Any]], object | Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    """Discoverable extension identity; discovery never executes its code."""

    extension_id: str
    version: str
    description: str
    capabilities: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    source: str = "builtin"
    trusted: bool = False

    def validate(self) -> None:
        _bounded_name(self.extension_id, "extension id")
        _bounded_text(self.version, "extension version", 128)
        _bounded_text(self.description, "extension description", 8_192)
        _text_tuple(self.capabilities, "extension capabilities")
        _text_tuple(self.tool_names, "extension tool names")
        _text_tuple(self.skill_names, "extension skill names")
        _bounded_text(self.source, "extension source", 4_096)
        if type(self.trusted) is not bool:
            raise ExtensionError("extension trusted flag must be boolean")

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return _sha256_json(
            {
                "schema": EXTENSION_MANIFEST_SCHEMA_V1,
                "extension_id": self.extension_id,
                "version": self.version,
                "description": self.description,
                "capabilities": self.capabilities,
                "tool_names": self.tool_names,
                "skill_names": self.skill_names,
                "source": self.source,
                "trusted": self.trusted,
            }
        )

    def summary(self) -> dict[str, object]:
        self.validate()
        return {
            "schema": EXTENSION_MANIFEST_SCHEMA_V1,
            "extension_id": self.extension_id,
            "version": self.version,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "tool_names": list(self.tool_names),
            "skill_names": list(self.skill_names),
            "source": self.source,
            "trusted": self.trusted,
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    """Lazy Agent Skills metadata; the body is read only on explicit load."""

    name: str
    description: str
    version: str
    path: str
    content_hash: str
    keywords: tuple[str, ...] = ()
    source: str = "filesystem"

    def validate(self) -> None:
        _bounded_name(self.name, "skill name")
        _bounded_text(self.description, "skill description", 8_192)
        _bounded_text(self.version, "skill version", 128)
        _bounded_text(self.path, "skill path", 8_192)
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise ExtensionError("skill content hash must be a lowercase SHA-256 digest")
        _text_tuple(self.keywords, "skill keywords")

    def summary(self) -> dict[str, object]:
        self.validate()
        return {
            "schema": SKILL_DESCRIPTOR_SCHEMA_V1,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "content_hash": self.content_hash,
            "keywords": list(self.keywords),
            "source": self.source,
        }


class SkillCatalog:
    """Discover skill metadata from trusted roots without importing code."""

    def __init__(self, descriptors: Iterable[SkillDescriptor] = ()) -> None:
        self._descriptors: dict[str, SkillDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: SkillDescriptor) -> None:
        descriptor.validate()
        if descriptor.name in self._descriptors:
            raise ExtensionError(f"skill name already registered: {descriptor.name}")
        self._descriptors[descriptor.name] = descriptor

    @classmethod
    def discover(
        cls,
        roots: Sequence[str | Path],
        *,
        max_files: int = 512,
        max_depth: int = 6,
        max_bytes: int = 512 * 1024,
    ) -> SkillCatalog:
        if type(max_files) is not int or not 1 <= max_files <= 10_000:
            raise ExtensionError("skill discovery max_files is invalid")
        if type(max_depth) is not int or not 0 <= max_depth <= 32:
            raise ExtensionError("skill discovery max_depth is invalid")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024 * 1024:
            raise ExtensionError("skill discovery max_bytes is invalid")
        catalog = cls()
        seen_paths: set[Path] = set()
        inspected = 0
        for raw_root in roots:
            root = Path(raw_root).expanduser().resolve()
            if root.is_symlink():
                continue
            if root.is_file() and root.name == "SKILL.md":
                candidates = [root]
            elif root.is_dir():
                candidates = root.rglob("SKILL.md")
            else:
                continue
            for path in candidates:
                if inspected >= max_files:
                    return catalog
                inspected += 1
                if path in seen_paths or path.is_symlink():
                    continue
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    continue
                if len(relative.parts) - 1 > max_depth or any(part in _SKIP_DIRECTORIES for part in relative.parts):
                    continue
                seen_paths.add(path)
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                if len(raw) > max_bytes:
                    continue
                metadata = _skill_frontmatter(raw)
                if metadata is None:
                    continue
                name, description, version, keywords = metadata
                if name in catalog._descriptors:
                    continue
                descriptor = SkillDescriptor(
                    name=name,
                    description=description,
                    version=version,
                    path=str(path),
                    content_hash=_sha256_bytes(raw),
                    keywords=keywords,
                    source=str(root),
                )
                try:
                    catalog.register(descriptor)
                except ExtensionError:
                    continue
        return catalog

    def list(self) -> tuple[SkillDescriptor, ...]:
        return tuple(self._descriptors[name] for name in sorted(self._descriptors))

    def select_for_task(self, task: str, *, limit: int = 8) -> tuple[SkillDescriptor, ...]:
        if type(task) is not str:
            raise ExtensionError("skill task must be text")
        if type(limit) is not int or not 0 <= limit <= 128:
            raise ExtensionError("skill selection limit is invalid")
        terms = {term for term in re.findall(r"[a-z0-9][a-z0-9_-]{1,63}", task.casefold())}
        ranked: list[tuple[int, SkillDescriptor]] = []
        for descriptor in self._descriptors.values():
            haystack = {descriptor.name, *descriptor.keywords, *descriptor.description.casefold().split()}
            score = sum(1 for term in terms if any(term in item.casefold() for item in haystack))
            if score:
                ranked.append((score, descriptor))
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        return tuple(item[1] for item in ranked[:limit])

    def load(self, name: str, *, max_bytes: int = 512 * 1024) -> str:
        normalized = _bounded_name(name, "skill name")
        descriptor = self._descriptors.get(normalized)
        if descriptor is None:
            raise ExtensionNotFound(f"skill is not registered: {normalized}")
        path = Path(descriptor.path).expanduser().resolve()
        if path.is_symlink() or not path.is_file():
            raise ExtensionError("skill path is no longer a regular file")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise ExtensionError("skill body exceeds its byte budget")
        if _sha256_bytes(raw) != descriptor.content_hash:
            raise ExtensionError("skill changed after discovery")
        return raw.decode("utf-8")

    def prompt_context(self, task: str, *, token_budget: int = 768, limit: int = 4) -> str:
        if type(token_budget) is not int or not 1 <= token_budget <= _MAX_PROMPT_WORDS:
            raise ExtensionError("skill prompt token budget is invalid")
        selected = self.select_for_task(task, limit=limit)
        if not selected:
            return ""
        chunks = ["AVAILABLE SKILLS (load full body only when needed):"]
        chunks.extend(
            f"- {descriptor.name}: {descriptor.description} [sha256={descriptor.content_hash[:12]}]"
            for descriptor in selected
        )
        words = " ".join(chunks).split()
        return " ".join(words[:token_budget])


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """Metadata for an MCP provider; the transport remains host-owned."""

    server_id: str
    description: str
    transport: str = "host"
    source: str = "local"
    approved: bool = False

    def validate(self) -> None:
        _bounded_name(self.server_id, "MCP server id")
        _bounded_text(self.description, "MCP server description", 8_192)
        _bounded_text(self.transport, "MCP transport", 128)
        _bounded_text(self.source, "MCP source", 4_096)
        if type(self.approved) is not bool:
            raise ExtensionError("MCP approval flag must be boolean")


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """A tool discovered from one MCP server before host registration."""

    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}))
    effect_class: str = "network_read"
    timeout_seconds: float = 30.0

    def validate(self) -> None:
        _bounded_name(self.name, "MCP tool name")
        _bounded_text(self.description, "MCP tool description", 8_192)
        if self.effect_class not in _EFFECT_CLASSES:
            raise ExtensionError("MCP tool effect class is unsupported")
        if (
            type(self.timeout_seconds) not in (int, float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 3_600
        ):
            raise ExtensionError("MCP tool timeout is invalid")
        schema = _json_value(self.input_schema, label="MCP tool input schema")
        if not isinstance(schema, dict):
            raise ExtensionError("MCP tool input schema must be a JSON object")


class McpToolProvider(Protocol):
    async def list_tools(self) -> Sequence[McpToolDescriptor]: ...

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> object: ...


@dataclass(frozen=True, slots=True)
class McpStdioConfig:
    """Explicit child-process configuration for the local MCP stdio transport."""

    command: tuple[str, ...]
    cwd: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    protocol_version: str = "2025-11-25"
    client_name: str = "aegis-cognition"
    client_version: str = "0.1.0"
    startup_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 30.0
    max_message_bytes: int = 4 * 1024 * 1024
    max_stderr_bytes: int = 64 * 1024
    max_tools: int = 256

    def validate(self) -> None:
        if type(self.command) not in (tuple, list) or not self.command:
            raise ExtensionError("MCP stdio command must be a non-empty sequence")
        for item in self.command:
            _bounded_text(item, "MCP stdio command item", 4_096)
            if "\x00" in item:
                raise ExtensionError("MCP stdio command cannot contain NUL")
        if self.cwd is not None:
            _bounded_text(self.cwd, "MCP stdio working directory", 8_192)
        if type(self.environment) not in (tuple, list):
            raise ExtensionError("MCP stdio environment must be a sequence")
        keys: set[str] = set()
        for item in self.environment:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise ExtensionError("MCP stdio environment entries must be key/value pairs")
            key, value = item
            if type(key) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key):
                raise ExtensionError("MCP stdio environment keys are invalid")
            if key in keys:
                raise ExtensionError("MCP stdio environment keys must be unique")
            keys.add(key)
            _bounded_text(value, "MCP stdio environment value", 8_192)
        _bounded_text(self.protocol_version, "MCP protocol version", 128)
        _bounded_text(self.client_name, "MCP client name", 128)
        _bounded_text(self.client_version, "MCP client version", 128)
        for value, label in (
            (self.startup_timeout_seconds, "MCP startup timeout"),
            (self.request_timeout_seconds, "MCP request timeout"),
        ):
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ExtensionError(f"{label} must be positive and finite")
            if value > 3_600:
                raise ExtensionError(f"{label} is too large")
        if type(self.max_message_bytes) is not int or not 1 <= self.max_message_bytes <= 16 * 1024 * 1024:
            raise ExtensionError("MCP message byte limit is invalid")
        if type(self.max_stderr_bytes) is not int or not 1 <= self.max_stderr_bytes <= 4 * 1024 * 1024:
            raise ExtensionError("MCP stderr byte limit is invalid")
        if type(self.max_tools) is not int or not 1 <= self.max_tools <= 4_096:
            raise ExtensionError("MCP tool count limit is invalid")


class McpStdioProvider:
    """Small, host-owned MCP stdio client for local approved servers.

    The provider deliberately supports the request/response subset needed for
    tool discovery and invocation. Server-initiated requests are rejected with
    a JSON-RPC method-not-found response rather than being executed implicitly.
    """

    def __init__(self, config: McpStdioConfig) -> None:
        if type(config) is not McpStdioConfig:
            raise TypeError("MCP stdio provider requires an McpStdioConfig")
        config.validate()
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next_request_id = 1
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buffer = bytearray()
        self._closed = False

    async def __aenter__(self) -> McpStdioProvider:
        await self.start()
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    async def start(self) -> None:
        self._assert_loop()
        async with self._lifecycle_lock:
            if self._closed:
                raise ExtensionError("MCP stdio provider is closed")
            if self._process is not None and self._process.returncode is None:
                return
            env = {key: value for key, value in self.config.environment}
            try:
                process = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        *self.config.command,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=self.config.cwd,
                        env=env,
                    ),
                    timeout=float(self.config.startup_timeout_seconds),
                )
            except (OSError, TimeoutError) as error:
                raise ExtensionError("MCP stdio server could not be started") from error
            self._process = process
            self._stderr_task = asyncio.create_task(self._drain_stderr(), name="aegis-mcp-stderr")
            try:
                await self._request(
                    "initialize",
                    {
                        "protocolVersion": self.config.protocol_version,
                        "capabilities": {},
                        "clientInfo": {
                            "name": self.config.client_name,
                            "version": self.config.client_version,
                        },
                    },
                    ensure_started=False,
                )
                await self._write_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            except Exception:
                await self.close()
                raise

    async def close(self) -> None:
        self._closed = True
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.terminate()
                with suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                if process.returncode is None:
                    process.kill()
                    with suppress(Exception):
                        await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None

    async def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        await self.start()
        response = await self._request("tools/list", {})
        if not isinstance(response, Mapping):
            raise ExtensionError("MCP tools/list result must be an object")
        typed_response = cast(Mapping[str, object], response)
        raw_tools = typed_response.get("tools", ())
        if type(raw_tools) is not list:
            raise ExtensionError("MCP tools/list result must contain a tools list")
        typed_raw_tools = cast(list[object], raw_tools)
        if len(typed_raw_tools) > self.config.max_tools:
            raise ExtensionError("MCP tools/list result exceeds its tool count limit")
        descriptors: list[McpToolDescriptor] = []
        for raw_tool in typed_raw_tools:
            if not isinstance(raw_tool, Mapping):
                raise ExtensionError("MCP tool descriptor must be an object")
            typed_tool = cast(Mapping[str, object], raw_tool)
            raw_name = typed_tool.get("name")
            raw_description = typed_tool.get("description", "MCP tool")
            raw_schema = typed_tool.get("inputSchema", {})
            if type(raw_name) is not str or type(raw_description) is not str or not isinstance(raw_schema, Mapping):
                raise ExtensionError("MCP tool descriptor has invalid fields")
            descriptors.append(
                McpToolDescriptor(
                    name=_bounded_name(raw_name, "MCP tool name"),
                    description=_bounded_text(raw_description, "MCP tool description", 8_192),
                    input_schema=cast(Mapping[str, Any], raw_schema),
                    timeout_seconds=self.config.request_timeout_seconds,
                )
            )
        return tuple(descriptors)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> object:
        await self.start()
        normalized = _bounded_name(name, "MCP tool name")
        result = await self._request(
            "tools/call",
            {
                "name": normalized,
                "arguments": cast(dict[str, Any], _json_value(dict(arguments), label="MCP arguments")),
            },
        )
        return result

    def _assert_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ExtensionError("MCP stdio provider cannot be shared across event loops")

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        ensure_started: bool = True,
    ) -> object:
        self._assert_loop()
        if ensure_started:
            await self.start()
        async with self._request_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            await self._write_message({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                message = await self._read_message()
                if message.get("id") != request_id:
                    await self._handle_server_message(message)
                    continue
                if message.get("jsonrpc") != "2.0":
                    raise ExtensionError("MCP response has an invalid JSON-RPC version")
                if "error" in message:
                    error = message["error"]
                    raise ExtensionError(f"MCP request failed: {self._error_text(error)}")
                if "result" not in message:
                    raise ExtensionError("MCP response has no result")
                return message["result"]

    async def _write_message(self, message: Mapping[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise ExtensionError("MCP stdio server is not running")
        try:
            encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ExtensionError("MCP message is not JSON-compatible") from error
        if b"\n" in encoded or len(encoded) > self.config.max_message_bytes:
            raise ExtensionError("MCP message exceeds its byte boundary")
        async with self._write_lock:
            process.stdin.write(encoded + b"\n")
            try:
                await asyncio.wait_for(process.stdin.drain(), timeout=float(self.config.request_timeout_seconds))
            except (BrokenPipeError, ConnectionError, TimeoutError) as error:
                raise ExtensionError("MCP stdio write failed") from error

    async def _read_message(self) -> dict[str, object]:
        process = self._process
        if process is None or process.stdout is None:
            raise ExtensionError("MCP stdio server is not running")
        try:
            raw = await asyncio.wait_for(process.stdout.readline(), timeout=float(self.config.request_timeout_seconds))
        except TimeoutError as error:
            raise ExtensionError("MCP stdio response timed out") from error
        if not raw:
            raise ExtensionError("MCP stdio server closed stdout")
        if len(raw) > self.config.max_message_bytes + 1:
            raise ExtensionError("MCP stdio response exceeds its byte boundary")
        if not raw.endswith(b"\n"):
            raise ExtensionError("MCP stdio response is not newline-delimited")
        try:
            decoded = json.loads(raw[:-1].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExtensionError("MCP stdio response is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ExtensionError("MCP stdio response must be a JSON object")
        return cast(dict[str, object], decoded)

    async def _handle_server_message(self, message: Mapping[str, object]) -> None:
        method = message.get("method")
        if type(method) is not str:
            return
        if "id" in message:
            request_id = message["id"]
            await self._write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "server requests are not supported by this host"},
                }
            )

    @staticmethod
    def _error_text(error: object) -> str:
        if isinstance(error, Mapping):
            typed_error = cast(Mapping[str, object], error)
            message = typed_error.get("message")
            if type(message) is str and message:
                return message[:1_024]
        return "unknown MCP error"

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            chunk = await process.stderr.read(4_096)
            if not chunk:
                return
            self._stderr_buffer.extend(chunk)
            if len(self._stderr_buffer) > self.config.max_stderr_bytes:
                del self._stderr_buffer[: len(self._stderr_buffer) - self.config.max_stderr_bytes]

    @property
    def stderr_snapshot(self) -> str:
        return self._stderr_buffer.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class McpHttpConfig:
    """Bounded configuration for the MCP Streamable HTTP transport.

    Remote hosts must be explicitly allowlisted.  Local or plain-HTTP
    endpoints additionally require ``allow_local`` and are restricted to
    loopback addresses.  Authentication headers are intentionally explicit;
    the client never reads browser cookies or ambient process credentials.
    """

    endpoint: str
    allowed_hosts: tuple[str, ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    allow_local: bool = False
    protocol_version: str = "2025-11-25"
    client_name: str = "aegis-cognition"
    client_version: str = "0.1.0"
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 30.0
    max_response_bytes: int = 4 * 1024 * 1024
    max_header_bytes: int = 64 * 1024
    max_tools: int = 256

    def validate(self) -> None:
        parsed = _parse_mcp_endpoint(self.endpoint)
        if parsed.username is not None or parsed.password is not None:
            raise ExtensionError("MCP HTTP endpoint must not contain credentials")
        if parsed.fragment:
            raise ExtensionError("MCP HTTP endpoint must not contain a fragment")
        if parsed.scheme not in {"https", "http"}:
            raise ExtensionError("MCP HTTP endpoint must use HTTPS or explicit local HTTP")
        if not parsed.hostname:
            raise ExtensionError("MCP HTTP endpoint must contain a host")
        try:
            port = parsed.port
        except ValueError as error:
            raise ExtensionError("MCP HTTP endpoint port is invalid") from error
        if port is not None and not 1 <= port <= 65_535:
            raise ExtensionError("MCP HTTP endpoint port is invalid")
        if type(self.allowed_hosts) not in (tuple, list):
            raise ExtensionError("MCP HTTP allowed_hosts must be a sequence")
        if any(type(item) is not str for item in self.allowed_hosts):
            raise ExtensionError("MCP HTTP allowed hosts must be strings")
        normalized_hosts = tuple(item.strip().casefold().rstrip(".") for item in self.allowed_hosts)
        if len(normalized_hosts) != len(set(normalized_hosts)):
            raise ExtensionError("MCP HTTP allowed_hosts must be unique")
        for host in normalized_hosts:
            if not host or any(char in host for char in "\\/\x00"):
                raise ExtensionError("MCP HTTP allowed host is invalid")
        if parsed.hostname.casefold().rstrip(".") not in normalized_hosts:
            raise ExtensionError("MCP HTTP endpoint host must be explicitly allowlisted")
        if type(self.headers) not in (tuple, list):
            raise ExtensionError("MCP HTTP headers must be a sequence")
        protected = {
            "accept",
            "content-length",
            "content-type",
            "connection",
            "host",
            "mcp-protocol-version",
            "mcp-session-id",
            "origin",
        }
        names: set[str] = set()
        for item in self.headers:
            if type(item) not in (tuple, list) or len(item) != 2:
                raise ExtensionError("MCP HTTP headers must be key/value pairs")
            name, value = item
            if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", name):
                raise ExtensionError("MCP HTTP header name is invalid")
            if name.casefold() in protected or name.casefold() in names:
                raise ExtensionError("MCP HTTP header is reserved or duplicated")
            if type(value) is not str or not value or "\r" in value or "\n" in value or len(value) > 16_384:
                raise ExtensionError("MCP HTTP header value is invalid")
            names.add(name.casefold())
        if type(self.allow_local) is not bool:
            raise ExtensionError("MCP HTTP allow_local must be boolean")
        _bounded_text(self.protocol_version, "MCP protocol version", 128)
        _bounded_text(self.client_name, "MCP HTTP client name", 128)
        _bounded_text(self.client_version, "MCP HTTP client version", 128)
        for value, label in (
            (self.connect_timeout_seconds, "MCP HTTP connect timeout"),
            (self.request_timeout_seconds, "MCP HTTP request timeout"),
        ):
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ExtensionError(f"{label} must be positive and finite")
            if value > 3_600:
                raise ExtensionError(f"{label} is too large")
        if type(self.max_response_bytes) is not int or not 1 <= self.max_response_bytes <= 16 * 1024 * 1024:
            raise ExtensionError("MCP HTTP response byte limit is invalid")
        if type(self.max_header_bytes) is not int or not 1 <= self.max_header_bytes <= 4 * 1024 * 1024:
            raise ExtensionError("MCP HTTP header byte limit is invalid")
        if type(self.max_tools) is not int or not 1 <= self.max_tools <= 4_096:
            raise ExtensionError("MCP HTTP tool count limit is invalid")
        if parsed.scheme == "http" and not self.allow_local:
            raise ExtensionError("plain HTTP MCP endpoints require allow_local=True")


def _parse_mcp_endpoint(endpoint: str) -> SplitResult:
    if type(endpoint) is not str or not endpoint or len(endpoint) > 8_192 or "\x00" in endpoint:
        raise ExtensionError("MCP HTTP endpoint is invalid")
    parsed = urlsplit(endpoint)
    if parsed.path == "":
        parsed = parsed._replace(path="/")
    return parsed


def _resolve_mcp_host(host: str, port: int, *, allow_local: bool) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = (str(literal),)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ExtensionError("MCP HTTP host could not be resolved") from error
        addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise ExtensionError("MCP HTTP host has no usable address")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise ExtensionError("MCP HTTP host resolved to an invalid address") from error
        if parsed.is_loopback:
            if not allow_local:
                raise ExtensionError("MCP HTTP loopback access requires allow_local=True")
        elif (
            parsed.is_private
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_unspecified
            or parsed.is_multicast
        ):
            raise ExtensionError("MCP HTTP host resolved to a private or reserved address")
    return addresses


class _PinnedHttpConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, resolved_address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._resolved_address = resolved_address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._resolved_address, self.port), self.timeout)


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, resolved_address: str, timeout: float) -> None:
        self._ssl_context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._ssl_context)
        self._resolved_address = resolved_address

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._resolved_address, self.port), self.timeout)
        try:
            self.sock = self._ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


class McpHttpProvider:
    """MCP Streamable HTTP client with bounded, fail-closed networking.

    The implementation supports JSON responses and finite SSE responses for
    request/response tool use.  It does not execute server-initiated requests,
    follow redirects, read ambient cookies, or silently trust private DNS
    addresses.  Long-lived notification streams are intentionally left to the
    host browser/runtime boundary rather than hidden inside tool invocation.
    """

    def __init__(self, config: McpHttpConfig) -> None:
        if type(config) is not McpHttpConfig:
            raise TypeError("MCP HTTP provider requires an McpHttpConfig")
        config.validate()
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._request_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._next_request_id = 1
        self._session_id: str | None = None
        self._negotiated_protocol: str | None = None
        self._started = False
        self._closed = False

    async def __aenter__(self) -> McpHttpProvider:
        await self.start()
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    async def start(self) -> None:
        self._assert_loop()
        async with self._lifecycle_lock:
            if self._closed:
                raise ExtensionError("MCP HTTP provider is closed")
            if self._started:
                return
            response, session_id = await self._request_once(
                "initialize",
                {
                    "protocolVersion": self.config.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": self.config.client_name, "version": self.config.client_version},
                },
                include_session=False,
                include_protocol=False,
                expect_response=True,
            )
            if not isinstance(response, Mapping):
                raise ExtensionError("MCP HTTP initialize result must be an object")
            raw_version = cast(Mapping[str, object], response).get("protocolVersion")
            if type(raw_version) is not str or raw_version != self.config.protocol_version:
                raise ExtensionError("MCP HTTP server selected an unsupported protocol version")
            self._session_id = session_id
            self._negotiated_protocol = raw_version
            await self._request_once(
                "notifications/initialized",
                {},
                include_session=True,
                include_protocol=True,
                expect_response=False,
            )
            self._started = True

    async def close(self) -> None:
        self._closed = True
        self._started = False
        session_id = self._session_id
        if session_id is not None and self._loop is not None:
            with suppress(Exception):
                await self._request_once(
                    "__delete_session__",
                    {},
                    include_session=True,
                    include_protocol=True,
                    expect_response=False,
                    method_override="DELETE",
                )
        self._session_id = None
        self._negotiated_protocol = None

    async def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        await self.start()
        response = await self._request("tools/list", {})
        if not isinstance(response, Mapping):
            raise ExtensionError("MCP HTTP tools/list result must be an object")
        raw_tools = cast(Mapping[str, object], response).get("tools", ())
        if type(raw_tools) is not list:
            raise ExtensionError("MCP HTTP tools/list result must contain a tools list")
        typed_raw_tools = cast(list[object], raw_tools)
        if len(typed_raw_tools) > self.config.max_tools:
            raise ExtensionError("MCP HTTP tools/list result exceeds its tool count limit")
        descriptors: list[McpToolDescriptor] = []
        for raw_tool in typed_raw_tools:
            if not isinstance(raw_tool, Mapping):
                raise ExtensionError("MCP HTTP tool descriptor must be an object")
            typed_tool = cast(Mapping[str, object], raw_tool)
            raw_name = typed_tool.get("name")
            raw_description = typed_tool.get("description", "MCP tool")
            raw_schema = typed_tool.get("inputSchema", {})
            if type(raw_name) is not str or type(raw_description) is not str or not isinstance(raw_schema, Mapping):
                raise ExtensionError("MCP HTTP tool descriptor has invalid fields")
            descriptor = McpToolDescriptor(
                name=_bounded_name(raw_name, "MCP tool name"),
                description=_bounded_text(raw_description, "MCP tool description", 8_192),
                input_schema=cast(Mapping[str, Any], raw_schema),
                timeout_seconds=self.config.request_timeout_seconds,
            )
            descriptor.validate()
            descriptors.append(descriptor)
        return tuple(descriptors)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> object:
        await self.start()
        normalized = _bounded_name(name, "MCP tool name")
        return await self._request(
            "tools/call",
            {
                "name": normalized,
                "arguments": cast(dict[str, Any], _json_value(dict(arguments), label="MCP arguments")),
            },
        )

    def _assert_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ExtensionError("MCP HTTP provider cannot be shared across event loops")

    async def _request(self, method: str, params: Mapping[str, Any]) -> object:
        for attempt in range(2):
            try:
                response, _session_id = await self._request_once(
                    method,
                    params,
                    include_session=True,
                    include_protocol=True,
                    expect_response=True,
                )
                return response
            except _McpSessionExpired as error:
                if attempt:
                    raise ExtensionError("MCP HTTP session expired during retry") from error
                async with self._lifecycle_lock:
                    self._started = False
                    self._session_id = None
                    self._negotiated_protocol = None
                await self.start()
        raise AssertionError("unreachable MCP HTTP request retry")

    async def _request_once(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        include_session: bool,
        include_protocol: bool,
        expect_response: bool,
        method_override: str = "POST",
    ) -> tuple[object | None, str | None]:
        self._assert_loop()
        async with self._request_lock:
            request_id: int | None = None
            payload: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}
            if expect_response:
                request_id = self._next_request_id
                self._next_request_id += 1
                payload["id"] = request_id
            session_for_request = self._session_id if include_session else None
            protocol_for_request = self._negotiated_protocol if include_protocol else None
            status, response_headers, body = await asyncio.to_thread(
                self._exchange,
                payload,
                session_for_request,
                protocol_for_request,
                method_override,
            )
            if status == 404 and include_session and self._session_id:
                raise _McpSessionExpired()
            if expect_response:
                if not 200 <= status < 300:
                    raise ExtensionError(f"MCP HTTP request failed with status {status}")
                messages = _parse_mcp_http_messages(body, response_headers)
                typed_message: Mapping[str, object] | None = None
                for message in messages:
                    if not isinstance(message, Mapping):
                        raise ExtensionError("MCP HTTP response must be a JSON object")
                    candidate = cast(Mapping[str, object], message)
                    if "id" not in candidate:
                        continue
                    if "method" in candidate:
                        raise ExtensionError("MCP HTTP server requests are not supported by this host")
                    if candidate.get("id") == request_id:
                        typed_message = candidate
                        break
                if typed_message is None:
                    raise ExtensionError("MCP HTTP response id does not match the request")
                if "error" in typed_message:
                    raise ExtensionError(f"MCP request failed: {McpStdioProvider._error_text(typed_message['error'])}")
                if "result" not in typed_message:
                    raise ExtensionError("MCP HTTP response has no result")
                session_id = _header_value(response_headers, "mcp-session-id")
                return typed_message["result"], session_id
            if status not in {200, 202, 204}:
                raise ExtensionError(f"MCP HTTP notification failed with status {status}")
            return None, _header_value(response_headers, "mcp-session-id")

    def _exchange(
        self,
        payload: Mapping[str, object],
        session_id: str | None,
        protocol_version: str | None,
        method: str,
    ) -> tuple[int, dict[str, str], bytes]:
        parsed = _parse_mcp_endpoint(self.config.endpoint)
        host = parsed.hostname
        if host is None:
            raise ExtensionError("MCP HTTP endpoint host is missing")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = _resolve_mcp_host(host, port, allow_local=self.config.allow_local)
        connection_cls: type[http.client.HTTPConnection] = (
            _PinnedHttpsConnection if parsed.scheme == "https" else _PinnedHttpConnection
        )
        connection = connection_cls(host, port, addresses[0], float(self.config.connect_timeout_seconds))
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Host": _http_host_header(parsed, port),
            "Connection": "close",
        }
        headers.update(dict(self.config.headers))
        if protocol_version is not None:
            headers["MCP-Protocol-Version"] = protocol_version
        if session_id is not None:
            headers["MCP-Session-Id"] = session_id
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > self.config.max_response_bytes:
            raise ExtensionError("MCP HTTP request exceeds its byte boundary")
        try:
            request_target = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
            connection.request(method, request_target, body, headers)
            response = connection.getresponse()
            header_bytes = sum(len(key) + len(value) + 4 for key, value in response.getheaders())
            if header_bytes > self.config.max_header_bytes:
                raise ExtensionError("MCP HTTP response headers exceed their byte boundary")
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > self.config.max_response_bytes:
                        raise ExtensionError("MCP HTTP response exceeds its byte boundary")
                except ValueError as error:
                    raise ExtensionError("MCP HTTP response Content-Length is invalid") from error
            response_body = response.read(self.config.max_response_bytes + 1)
            if len(response_body) > self.config.max_response_bytes:
                raise ExtensionError("MCP HTTP response exceeds its byte boundary")
            response_headers = {key.casefold(): value for key, value in response.getheaders()}
            return response.status, response_headers, response_body
        except (OSError, http.client.HTTPException, TimeoutError) as error:
            raise ExtensionError("MCP HTTP exchange failed") from error
        finally:
            connection.close()


class _McpSessionExpired(Exception):
    pass


def _http_host_header(parsed: SplitResult, port: int) -> str:
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name.casefold())
    if value is None:
        return None
    if not value or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        raise ExtensionError("MCP HTTP response header contains invalid characters")
    if len(value) > 512:
        raise ExtensionError("MCP HTTP response header is too large")
    return value


def _parse_mcp_http_messages(body: bytes, headers: Mapping[str, str]) -> tuple[object, ...]:
    content_type = headers.get("content-type", "application/json").casefold().split(";", 1)[0].strip()
    if content_type == "text/event-stream":
        text = body.decode("utf-8", errors="strict")
        messages: list[object] = []
        for event in re.split(r"\r?\n\r?\n", text):
            data_lines = [line[5:] for line in event.splitlines() if line.startswith("data:")]
            if not data_lines:
                continue
            try:
                messages.append(json.loads("\n".join(data_lines)))
            except json.JSONDecodeError as error:
                raise ExtensionError("MCP HTTP SSE data is not valid JSON") from error
        if not messages:
            raise ExtensionError("MCP HTTP SSE response contained no JSON-RPC message")
        return tuple(messages)
    if content_type != "application/json":
        raise ExtensionError("MCP HTTP response has an unsupported content type")
    try:
        return (json.loads(body.decode("utf-8")),)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExtensionError("MCP HTTP response is not valid JSON") from error


@dataclass(slots=True)
class _ToolRegistration:
    spec: ToolSpec
    handler: ToolHandler
    serial_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = field(
        default_factory=lambda: cast(
            weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock],
            weakref.WeakKeyDictionary(),
        )
    )
    serial_lock_guard: threading.Lock = field(default_factory=threading.Lock)

    def lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self.serial_lock_guard:
            lock = self.serial_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self.serial_locks[loop] = lock
            return lock


class ExtensionRegistry:
    """Thread-safe metadata registry with lazy, bounded async tool dispatch."""

    def __init__(self, *, skill_catalog: object | None = None, max_tools: int = 256) -> None:
        if type(max_tools) is not int or not 1 <= max_tools <= 4_096:
            raise ExtensionError("extension registry max_tools is invalid")
        if skill_catalog is not None and type(skill_catalog) is not SkillCatalog:
            raise TypeError("skill_catalog must be a SkillCatalog")
        self._max_tools = max_tools
        self._manifests: dict[str, ExtensionManifest] = {}
        self._tools: dict[str, _ToolRegistration] = {}
        self._lock = threading.RLock()
        self.skill_catalog = skill_catalog if skill_catalog is not None else SkillCatalog()

    def register(
        self,
        manifest: ExtensionManifest,
        tools: Iterable[tuple[object, object]] = (),
    ) -> None:
        manifest.validate()
        normalized_id = _bounded_name(manifest.extension_id, "extension id")
        if normalized_id != manifest.extension_id:
            raise ExtensionError("extension id must already be normalized")
        registrations = tuple(tools)
        local_names: set[str] = set()
        normalized_registrations: list[tuple[ToolSpec, ToolHandler]] = []
        for raw_spec, raw_handler in registrations:
            if type(raw_spec) is not ToolSpec or not callable(raw_handler):
                raise ExtensionError("extension tools require a ToolSpec and callable handler")
            spec = raw_spec
            handler = cast(ToolHandler, raw_handler)
            spec.validate()
            if spec.extension_id != manifest.extension_id:
                raise ExtensionError("tool extension id does not match manifest")
            if spec.name in local_names:
                raise ExtensionError(f"tool name already registered: {spec.name}")
            local_names.add(spec.name)
            normalized_registrations.append((spec, handler))
        if tuple(sorted(local_names)) != manifest.tool_names:
            raise ExtensionError("manifest tool_names do not match registrations")
        with self._lock:
            if normalized_id in self._manifests:
                raise ExtensionError(f"extension id already registered: {normalized_id}")
            if len(self._tools) + len(normalized_registrations) > self._max_tools:
                raise ExtensionError("extension registry tool capacity is exhausted")
            if any(spec.name in self._tools for spec, _ in normalized_registrations):
                raise ExtensionError("tool name already registered")
            self._manifests[normalized_id] = manifest
            for spec, handler in normalized_registrations:
                self._tools[spec.name] = _ToolRegistration(spec=spec, handler=handler)

    def unregister(self, extension_id: str) -> bool:
        normalized_id = _bounded_name(extension_id, "extension id")
        with self._lock:
            manifest = self._manifests.pop(normalized_id, None)
            if manifest is None:
                return False
            for name in manifest.tool_names:
                self._tools.pop(name, None)
            return True

    def manifests(self) -> tuple[ExtensionManifest, ...]:
        with self._lock:
            return tuple(self._manifests[name] for name in sorted(self._manifests))

    def tools(self) -> tuple[ToolSpec, ...]:
        with self._lock:
            return tuple(self._tools[name].spec for name in sorted(self._tools))

    def describe(self, *, task: str | None = None, limit: int = 32) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 0 <= limit <= self._max_tools:
            raise ExtensionError("tool description limit is invalid")
        specs = list(self.tools())
        if task:
            terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,63}", task.casefold()))
            specs.sort(
                key=lambda spec: (
                    -sum(1 for term in terms if term in f"{spec.name} {spec.description}".casefold()),
                    spec.name,
                )
            )
        return tuple(spec.summary() for spec in specs[:limit])

    def prompt_catalog(self, task: str, *, token_budget: int = 768, limit: int = 16) -> str:
        if type(token_budget) is not int or not 1 <= token_budget <= _MAX_PROMPT_WORDS:
            raise ExtensionError("extension prompt token budget is invalid")
        rows = self.describe(task=task, limit=limit)
        if not rows and not self.skill_catalog.list():
            return ""
        lines = ["AVAILABLE CAPABILITIES (metadata only; use typed tool_call):"]
        lines.extend(
            (f"- {row['name']}: {row['description']} [effect={row['effect_class']}; extension={row['extension_id']}]")
            for row in rows
        )
        skill_context = self.skill_catalog.prompt_context(task, token_budget=max(1, token_budget // 2))
        if skill_context:
            lines.append(skill_context)
        words = " ".join(lines).split()
        return " ".join(words[:token_budget])

    async def invoke(
        self,
        name: str,
        arguments: object | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        normalized = _bounded_name(name, "tool name")
        with self._lock:
            registration = self._tools.get(normalized)
        if registration is None:
            raise ExtensionNotFound(f"tool is not registered: {normalized}")
        args: object = {} if arguments is None else arguments
        if not isinstance(args, Mapping):
            raise ToolExecutionError("tool arguments must be a JSON object")
        typed_args = cast(Mapping[str, Any], args)
        detached_args = cast(Mapping[str, Any], _json_value(dict(typed_args), label="tool arguments"))
        effective_timeout = registration.spec.timeout_seconds if timeout_seconds is None else timeout_seconds
        if (
            type(effective_timeout) not in (int, float)
            or isinstance(effective_timeout, bool)
            or not 0 < float(effective_timeout) <= registration.spec.timeout_seconds
        ):
            raise ToolExecutionError("tool timeout exceeds the registered limit")

        async def call() -> object:
            if inspect.iscoroutinefunction(registration.handler):
                result = registration.handler(detached_args)
                return await cast(Awaitable[object], result)
            result = await asyncio.to_thread(registration.handler, detached_args)
            if inspect.isawaitable(result):
                return await cast(Awaitable[object], result)
            return result

        if registration.spec.concurrency == "serial":
            async with registration.lock_for_current_loop():
                result = await asyncio.wait_for(call(), timeout=float(effective_timeout))
        else:
            result = await asyncio.wait_for(call(), timeout=float(effective_timeout))
        bounded = _json_value(result, label="tool result", max_bytes=registration.spec.max_result_bytes)
        return bounded

    async def tool_runner(self, request: object, **_: Any) -> object:
        if not isinstance(request, Mapping):
            raise ToolExecutionError("tool request must be a mapping")
        typed_request = cast(Mapping[str, Any], request)
        raw_name = typed_request.get("tool_name", typed_request.get("name"))
        if type(raw_name) is not str:
            raise ToolExecutionError("tool request name is missing")
        raw_arguments = typed_request.get("input", typed_request.get("arguments", {}))
        if not isinstance(raw_arguments, Mapping):
            raise ToolExecutionError("tool request arguments must be an object")
        return await self.invoke(raw_name, cast(Mapping[str, Any], raw_arguments))

    async def register_mcp_provider(
        self,
        server: McpServerSpec,
        provider: object,
        *,
        activate: bool = False,
    ) -> tuple[ToolSpec, ...]:
        """Activate an MCP server only after explicit approval.

        A live provider may spawn a process or make a network request merely
        to answer ``tools/list``.  Therefore a pre-approval call is a strict
        no-op after validating the provider shape; metadata discovery belongs
        to ``McpServerSpec``/manifest files, not to a live transport.
        """

        server.validate()
        # Validate the two methods instead of relying on structural Protocol
        # checks at runtime; the provider may be implemented by another
        # package or process boundary.
        if not callable(getattr(provider, "list_tools", None)) or not callable(getattr(provider, "call_tool", None)):
            raise ExtensionError("MCP provider must expose list_tools and call_tool")
        if not activate:
            return ()
        if not server.approved:
            raise ExtensionError("MCP server is not approved for activation")
        typed_provider = cast(McpToolProvider, provider)
        descriptors = tuple(await typed_provider.list_tools())
        if len(descriptors) > self._max_tools:
            raise ExtensionError("MCP tool catalog exceeds the registry bound")
        specs: list[ToolSpec] = []
        for descriptor in descriptors:
            if type(descriptor) is not McpToolDescriptor:
                raise ExtensionError("MCP provider returned an invalid tool descriptor")
            descriptor.validate()
            tool_name = f"mcp.{server.server_id}.{descriptor.name}"
            specs.append(
                ToolSpec(
                    name=tool_name,
                    description=descriptor.description,
                    input_schema=descriptor.input_schema,
                    effect_class=descriptor.effect_class,
                    capabilities=(
                        "network_read" if descriptor.effect_class == "network_read" else descriptor.effect_class,
                    ),
                    timeout_seconds=descriptor.timeout_seconds,
                    extension_id=f"mcp.{server.server_id}",
                )
            )
        manifest = ExtensionManifest(
            extension_id=f"mcp.{server.server_id}",
            version="discovered",
            description=server.description,
            capabilities=("mcp", server.transport),
            tool_names=tuple(sorted(spec.name for spec in specs)),
            source=server.source,
            trusted=True,
        )

        def make_handler(descriptor: McpToolDescriptor) -> ToolHandler:
            async def handler(arguments: Mapping[str, Any]) -> object:
                return await typed_provider.call_tool(descriptor.name, arguments)

            return handler

        self.register(
            manifest,
            ((spec, make_handler(descriptor)) for spec, descriptor in zip(specs, descriptors, strict=True)),
        )
        return tuple(specs)


def discover_extension_manifests(
    roots: Sequence[str | Path],
    *,
    max_files: int = 256,
) -> tuple[ExtensionManifest, ...]:
    """Read local ``extension.toml`` files without importing or executing them."""

    if type(max_files) is not int or not 1 <= max_files <= 4_096:
        raise ExtensionError("extension discovery max_files is invalid")
    manifests: list[ExtensionManifest] = []
    seen: set[str] = set()
    count = 0
    for raw_root in roots:
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir() or root.is_symlink():
            continue
        candidates = [root / "extension.toml"] + [
            path for path in root.rglob("extension.toml") if path != root / "extension.toml"
        ]
        for path in candidates:
            if count >= max_files:
                return tuple(manifests)
            count += 1
            if path.is_symlink() or not path.is_file():
                continue
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                section = data.get("extension")
                if not isinstance(section, Mapping):
                    continue
                raw = cast(Mapping[str, Any], section)
                extension_id = _bounded_name(raw.get("id"), "extension id")
                if extension_id in seen:
                    continue
                manifest = ExtensionManifest(
                    extension_id=extension_id,
                    version=_bounded_text(raw.get("version"), "extension version", 128),
                    description=_bounded_text(raw.get("description"), "extension description", 8_192),
                    capabilities=_text_tuple(raw.get("capabilities", ()), "extension capabilities"),
                    tool_names=_text_tuple(raw.get("tools", ()), "extension tools"),
                    skill_names=_text_tuple(raw.get("skills", ()), "extension skills"),
                    source=str(path),
                    trusted=False,
                )
                manifest.validate()
            except OSError, tomllib.TOMLDecodeError, ExtensionError:
                continue
            seen.add(extension_id)
            manifests.append(manifest)
    return tuple(manifests)


def _skill_frontmatter(raw: bytes) -> tuple[str, str, str, tuple[str, ...]] | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(index for index in range(1, min(len(lines), 128)) if lines[index].strip() == "---")
    except StopIteration:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        if key in {"name", "description", "version", "keywords"}:
            fields[key] = value
    try:
        name = _bounded_name(fields.get("name"), "skill name")
        description = _bounded_text(fields.get("description"), "skill description", 8_192)
        version = _bounded_text(fields.get("version", "1.0.0"), "skill version", 128)
    except ExtensionError:
        return None
    keywords = tuple(sorted({item.strip().lower() for item in fields.get("keywords", "").split(",") if item.strip()}))
    try:
        _text_tuple(keywords, "skill keywords")
    except ExtensionError:
        return None
    return name, description, version, keywords


__all__ = [
    "EXTENSION_MANIFEST_SCHEMA_V1",
    "MCP_SERVER_SCHEMA_V1",
    "SKILL_DESCRIPTOR_SCHEMA_V1",
    "TOOL_DESCRIPTOR_SCHEMA_V1",
    "ExtensionError",
    "ExtensionManifest",
    "ExtensionNotFound",
    "ExtensionRegistry",
    "McpHttpConfig",
    "McpHttpProvider",
    "McpServerSpec",
    "McpStdioConfig",
    "McpStdioProvider",
    "McpToolDescriptor",
    "McpToolProvider",
    "SkillCatalog",
    "SkillDescriptor",
    "ToolExecutionError",
    "ToolHandler",
    "ToolSpec",
    "discover_extension_manifests",
]
