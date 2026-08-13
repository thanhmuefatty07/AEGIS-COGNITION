from dataclasses import dataclass

from .integration import validate_bridge_contract, validate_bridge_smoke
from .orchestrator import MessageFrame


@dataclass(frozen=True)
class ExternalServiceManifest:
    service_name: str
    version: str
    bridge_ready: bool
    contract_ok: bool
    runtime_surface_ok: bool
    packaging_ready: bool


def build_service_manifest(message: MessageFrame) -> ExternalServiceManifest:
    contract = validate_bridge_contract(message)
    smoke = validate_bridge_smoke(message)
    bridge_ready = contract.bridge_ok and smoke.overall_ok
    runtime_surface_ok = message.is_valid()
    packaging_ready = bridge_ready and runtime_surface_ok
    return ExternalServiceManifest(
        service_name="aegis-cognition",
        version="0.1.0",
        bridge_ready=bridge_ready,
        contract_ok=contract.bridge_ok,
        runtime_surface_ok=runtime_surface_ok,
        packaging_ready=packaging_ready,
    )
