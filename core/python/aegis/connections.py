"""Local-first connection and model catalog bridge.

Only opaque ``secret_ref`` values cross this API. Secret material is owned by
the platform credential adapter and is never serialized into the catalog.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any

from .native import native_module
from .discovery import (
    MAX_DISCOVERY_BODY_BYTES,
    DISCOVERY_TIMEOUT_SECONDS,
    DiscoveryRequester,
    EgressCheck,
    SecretResolver,
    discover_connection_models,
)
from .provider_models import reasoning_efforts_from_capabilities


# Model metadata changes much less often than a chat turn.  Keep the last
# successful discovery in the native catalog and reuse it for a short window;
# callers can still force a refresh when the user explicitly asks for one.
MODEL_DISCOVERY_CACHE_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class ConnectionRecord:
    connection_id: str
    provider_kind: str
    endpoint: str
    protocol: str
    secret_ref: str | None
    enabled: bool
    revision: int
    updated_at_ms: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConnectionRecord:
        return cls(
            connection_id=str(data["connection_id"]),
            provider_kind=str(data["provider_kind"]),
            endpoint=str(data["endpoint"]),
            protocol=str(data["protocol"]),
            secret_ref=(str(data["secret_ref"]) if data.get("secret_ref") is not None else None),
            enabled=bool(data["enabled"]),
            revision=int(data["revision"]),
            updated_at_ms=int(data["updated_at_ms"]),
        )


@dataclass(frozen=True)
class ModelDescriptor:
    connection_id: str
    model_id: str
    family: str | None
    capabilities: tuple[str, ...]
    context_limit: int | None
    output_limit: int | None
    source: str
    revision: int
    observed_at_ms: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ModelDescriptor:
        return cls(
            connection_id=str(data["connection_id"]),
            model_id=str(data["model_id"]),
            family=(str(data["family"]) if data.get("family") is not None else None),
            capabilities=tuple(str(value) for value in data.get("capabilities", [])),
            context_limit=(int(data["context_limit"]) if data.get("context_limit") is not None else None),
            output_limit=(int(data["output_limit"]) if data.get("output_limit") is not None else None),
            source=str(data["source"]),
            revision=int(data["revision"]),
            observed_at_ms=int(data["observed_at_ms"]),
        )

    @property
    def reasoning_efforts(self) -> tuple[str, ...]:
        return reasoning_efforts_for_model(self)

    @property
    def supports_vision(self) -> bool:
        return supports_vision_for_model(self)

    @property
    def input_modalities(self) -> tuple[str, ...]:
        return input_modalities_for_model(self)


def _model_capabilities(model: Any) -> tuple[str, ...]:
    value = getattr(model, "capabilities", ())
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(str(item).casefold() for item in value if isinstance(item, str))


def reasoning_efforts_for_model(model: Any) -> tuple[str, ...]:
    """Return only effort levels explicitly advertised by the provider.

    Model-name heuristics are useful for search and diagnostics, but they are
    not a contract for an account, endpoint, or deployment.  Keeping them out
    of this list makes the picker and request validation fail closed: an
    unadvertised model remains usable with ``Auto`` but cannot receive a
    guessed provider-specific control.
    """

    capabilities = getattr(model, "capabilities", ())
    return reasoning_efforts_from_capabilities(capabilities)


def supports_vision_for_model(model: Any) -> bool:
    capabilities = _model_capabilities(model)
    # Older catalogs used to persist ``input:image`` together with the
    # name-only ``vision:inferred`` marker.  Treat that legacy combination as
    # unverified until the next authenticated catalog refresh replaces it.
    if "vision:inferred" in capabilities and "vision" not in capabilities and "capability:provider-profile" not in capabilities:
        return False
    return any(
        marker in capabilities
        for marker in ("vision", "input:image", "image", "multimodal")
    )


def max_image_inputs_for_model(model: Any) -> int | None:
    """Return an explicitly known per-model image count limit, if present."""

    for capability in _model_capabilities(model):
        prefix = "vision:max-images:"
        if capability.startswith(prefix):
            raw_limit = capability[len(prefix) :]
            if raw_limit.isdecimal():
                limit = int(raw_limit)
                if 1 <= limit <= 4:
                    return limit
    return None


def input_modalities_for_model(model: Any) -> tuple[str, ...]:
    modalities = ["text"]
    if supports_vision_for_model(model):
        modalities.append("image")
    return tuple(modalities)


@dataclass(frozen=True)
class EgressGrant:
    grant_id: str
    connection_id: str
    connection_revision: int
    data_class: str
    operation: str
    expires_at_ms: int | None
    revision: int
    updated_at_ms: int
    revoked_at_ms: int | None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> EgressGrant:
        return cls(
            grant_id=str(data["grant_id"]),
            connection_id=str(data["connection_id"]),
            connection_revision=int(data["connection_revision"]),
            data_class=str(data["data_class"]),
            operation=str(data["operation"]),
            expires_at_ms=(int(data["expires_at_ms"]) if data.get("expires_at_ms") is not None else None),
            revision=int(data["revision"]),
            updated_at_ms=int(data["updated_at_ms"]),
            revoked_at_ms=(int(data["revoked_at_ms"]) if data.get("revoked_at_ms") is not None else None),
        )


class ConnectionCatalog:
    """Typed Python adapter over the Rust-owned local catalog."""

    def __init__(self, rust_bridge: Any = None) -> None:
        self._bridge = rust_bridge

    @property
    def _native(self) -> Any:
        if self._bridge is None:
            self._bridge = native_module()
        return self._bridge

    def register_connection(
        self,
        connection_id: str,
        *,
        provider_kind: str,
        endpoint: str,
        protocol: str,
        secret_ref: str | None = None,
        enabled: bool = True,
        expected_revision: int | None = None,
        timestamp: int | None = None,
    ) -> ConnectionRecord:
        self._require_text(connection_id, "connection_id")
        self._require_text(provider_kind, "provider_kind")
        self._require_text(endpoint, "endpoint")
        self._require_text(protocol, "protocol")
        if secret_ref is not None:
            self._require_text(secret_ref, "secret_ref")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_upsert_connection(
            connection_id,
            provider_kind,
            endpoint,
            protocol,
            secret_ref,
            bool(enabled),
            expected_revision,
            event_time,
        )
        return ConnectionRecord.from_mapping(json.loads(raw)["record"])

    def list_connections(self) -> tuple[ConnectionRecord, ...]:
        raw = json.loads(self._native.aegis_list_connections())
        return tuple(
            ConnectionRecord.from_mapping(item)
            for item in raw.get("records", [])
            if isinstance(item, dict)
        )

    def discover_models(
        self,
        connection_id: str,
        *,
        secret_resolver: SecretResolver | None = None,
        requester: DiscoveryRequester | None = None,
        egress_check: EgressCheck | None = None,
        timeout_seconds: float = DISCOVERY_TIMEOUT_SECONDS,
        max_body_bytes: int = MAX_DISCOVERY_BODY_BYTES,
        timestamp: int | None = None,
        force_refresh: bool = False,
        cache_ttl_seconds: float = MODEL_DISCOVERY_CACHE_TTL_SECONDS,
        now_ms: int | None = None,
    ) -> tuple[ModelDescriptor, ...]:
        self._require_text(connection_id, "connection_id")
        if type(force_refresh) is not bool:
            raise ValueError("force_refresh must be boolean")
        if (
            type(cache_ttl_seconds) not in (int, float)
            or not math.isfinite(float(cache_ttl_seconds))
            or float(cache_ttl_seconds) < 0
        ):
            raise ValueError("cache_ttl_seconds must be a finite non-negative number")
        if now_ms is not None and (type(now_ms) is not int or now_ms < 0):
            raise ValueError("now_ms must be a non-negative integer when provided")
        connection = next(
            (record for record in self.list_connections() if record.connection_id == connection_id),
            None,
        )
        if connection is None:
            raise ValueError("connection_id was not found")
        if not connection.enabled:
            raise ValueError("disabled connections cannot be discovered")
        existing_records = self.list_models(connection_id)
        observed_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        has_fresh_discovery = any(
            record.source == "discovered"
            and 0 <= observed_now_ms - record.observed_at_ms <= int(float(cache_ttl_seconds) * 1000)
            for record in existing_records
        )
        if existing_records and has_fresh_discovery and not force_refresh:
            return existing_records
        discovered = discover_connection_models(
            connection,
            secret_resolver=secret_resolver,
            requester=requester,
            egress_check=egress_check,
            timeout_seconds=timeout_seconds,
            max_body_bytes=max_body_bytes,
        )
        existing = {record.model_id: record for record in existing_records}
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        models = tuple(
            self.register_model(
                connection_id,
                str(item["model_id"]),
                family=item["family"],
                capabilities=tuple(str(value) for value in item["capabilities"]),
                context_limit=item["context_limit"],
                output_limit=item["output_limit"],
                source="discovered",
                expected_revision=(existing[item["model_id"]].revision if item["model_id"] in existing else None),
                timestamp=event_time,
            )
            for item in discovered
        )
        # A successful non-empty refresh is the authoritative catalog for the
        # current credential.  Remove only stale discovered rows; manually
        # registered models remain available for local/custom workflows.  An
        # empty response deliberately keeps the previous catalog so a failed
        # reconnect can roll back without destroying the last known-good state.
        if models:
            current_ids = {model.model_id for model in models}
            for record in existing_records:
                if record.source == "discovered" and record.model_id not in current_ids:
                    self.remove_model(
                        connection_id,
                        record.model_id,
                        expected_revision=record.revision,
                    )
        return models

    def remove_model(self, connection_id: str, model_id: str, *, expected_revision: int) -> bool:
        self._require_text(connection_id, "connection_id")
        self._require_text(model_id, "model_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        raw = self._native.aegis_remove_model_descriptor(connection_id, model_id, expected_revision)
        return bool(json.loads(raw)["removed"])

    def revoke_connection(
        self,
        connection_id: str,
        *,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> ConnectionRecord:
        self._require_text(connection_id, "connection_id")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_revoke_connection(connection_id, int(expected_revision), event_time)
        return ConnectionRecord.from_mapping(json.loads(raw)["record"])

    def register_model(
        self,
        connection_id: str,
        model_id: str,
        *,
        family: str | None = None,
        capabilities: tuple[str, ...] | list[str] = (),
        context_limit: int | None = None,
        output_limit: int | None = None,
        source: str = "manual",
        expected_revision: int | None = None,
        timestamp: int | None = None,
    ) -> ModelDescriptor:
        self._require_text(connection_id, "connection_id")
        self._require_text(model_id, "model_id")
        if family is not None:
            self._require_text(family, "family")
        if any(not isinstance(value, str) or not value.strip() for value in capabilities):
            raise ValueError("capabilities must contain non-empty strings")
        if context_limit is not None and int(context_limit) < 1:
            raise ValueError("context_limit must be positive when provided")
        if output_limit is not None and int(output_limit) < 1:
            raise ValueError("output_limit must be positive when provided")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_upsert_model_descriptor(
            connection_id,
            model_id,
            family,
            json.dumps(list(capabilities), sort_keys=True),
            context_limit,
            output_limit,
            source,
            expected_revision,
            event_time,
        )
        return ModelDescriptor.from_mapping(json.loads(raw)["record"])

    def list_models(self, connection_id: str) -> tuple[ModelDescriptor, ...]:
        self._require_text(connection_id, "connection_id")
        raw = json.loads(self._native.aegis_list_model_descriptors(connection_id))
        return tuple(
            ModelDescriptor.from_mapping(item)
            for item in raw.get("records", [])
            if isinstance(item, dict)
        )

    def get_egress(
        self,
        connection_id: str,
        data_class: str,
        operation: str,
    ) -> EgressGrant | None:
        """Read one grant so a reconnect can update it with its revision."""

        for value, name in (
            (connection_id, "connection_id"),
            (data_class, "data_class"),
            (operation, "operation"),
        ):
            self._require_text(value, name)
        raw = self._native.aegis_get_connection_egress(connection_id, data_class, operation)
        record = json.loads(raw).get("record")
        return EgressGrant.from_mapping(record) if isinstance(record, dict) else None

    def grant_egress(
        self,
        grant_id: str,
        connection_id: str,
        data_class: str,
        operation: str,
        *,
        expires_at_ms: int | None = None,
        expected_revision: int | None = None,
        timestamp: int | None = None,
    ) -> EgressGrant:
        for value, name in (
            (grant_id, "grant_id"),
            (connection_id, "connection_id"),
            (data_class, "data_class"),
            (operation, "operation"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_grant_connection_egress(
            grant_id,
            connection_id,
            data_class,
            operation,
            expires_at_ms,
            expected_revision,
            event_time,
        )
        return EgressGrant.from_mapping(json.loads(raw)["record"])

    def revoke_egress(
        self,
        grant_id: str,
        *,
        expected_revision: int,
        timestamp: int | None = None,
    ) -> EgressGrant:
        self._require_text(grant_id, "grant_id")
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_revoke_connection_egress(grant_id, int(expected_revision), event_time)
        return EgressGrant.from_mapping(json.loads(raw)["record"])

    def check_egress(
        self,
        connection_id: str,
        data_class: str,
        operation: str,
        *,
        timestamp: int | None = None,
    ) -> bool:
        for value, name in (
            (connection_id, "connection_id"),
            (data_class, "data_class"),
            (operation, "operation"),
        ):
            self._require_text(value, name)
        event_time = int(time.time() * 1000) if timestamp is None else int(timestamp)
        raw = self._native.aegis_check_connection_egress(
            connection_id,
            data_class,
            operation,
            event_time,
        )
        return bool(json.loads(raw)["allowed"])

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be non-empty")


__all__ = [
    "ConnectionCatalog",
    "ConnectionRecord",
    "EgressGrant",
    "ModelDescriptor",
    "input_modalities_for_model",
    "max_image_inputs_for_model",
    "reasoning_efforts_for_model",
    "supports_vision_for_model",
]
