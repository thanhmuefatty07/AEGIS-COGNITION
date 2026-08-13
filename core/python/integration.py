from dataclasses import dataclass

from .orchestrator import MessageFrame, ZeroCopyFrame, build_message, to_zero_copy, validate_runtime_message


RUST_SCHEMA_ID = 0xAE1515
RUST_SCHEMA_VERSION = 1
RUST_ALIGNMENT = 64
RUST_HEADER_BYTES = 64


@dataclass(frozen=True)
class BridgeContractResult:
    message_ok: bool
    zero_copy_ok: bool
    schema_ok: bool
    alignment_ok: bool
    bridge_ok: bool
    layout_ok: bool
    metadata_ok: bool
    descriptor_ok: bool


@dataclass(frozen=True)
class BridgeSmokeResult:
    contract: BridgeContractResult
    payload_size_ok: bool
    identity_ok: bool
    overall_ok: bool


@dataclass(frozen=True)
class BridgeBatchSmokeResult:
    contract: BridgeContractResult
    total_messages: int
    passed_messages: int
    overall_ok: bool


def validate_bridge_contract(message: MessageFrame) -> BridgeContractResult:
    message_ok = validate_runtime_message(message)
    zero_copy = to_zero_copy(message) if message_ok else None
    zero_copy_ok = zero_copy.is_valid() if zero_copy else False
    schema_ok = message.message_id > 0 and message.session_id > 0 and RUST_SCHEMA_ID == 0xAE1515 and RUST_SCHEMA_VERSION == 1
    alignment_ok = RUST_ALIGNMENT == 64
    layout_ok = RUST_HEADER_BYTES == 64
    metadata_ok = layout_ok and alignment_ok and schema_ok
    descriptor_ok = message.payload_len > 0
    bridge_ok = message_ok and zero_copy_ok and metadata_ok and descriptor_ok
    return BridgeContractResult(
        message_ok=message_ok,
        zero_copy_ok=zero_copy_ok,
        schema_ok=schema_ok,
        alignment_ok=alignment_ok,
        bridge_ok=bridge_ok,
        layout_ok=layout_ok,
        metadata_ok=metadata_ok,
        descriptor_ok=descriptor_ok,
    )


def validate_bridge_smoke(message: MessageFrame) -> BridgeSmokeResult:
    contract = validate_bridge_contract(message)
    payload_size_ok = message.payload_len >= 1
    identity_ok = message.message_id > 0 and message.session_id > 0
    overall_ok = contract.bridge_ok and payload_size_ok and identity_ok
    return BridgeSmokeResult(
        contract=contract,
        payload_size_ok=payload_size_ok,
        identity_ok=identity_ok,
        overall_ok=overall_ok,
    )


def validate_bridge_batch(messages: list[MessageFrame]) -> BridgeBatchSmokeResult:
    if not messages:
        empty_contract = BridgeContractResult(False, False, False, False, False, False, False, False)
        return BridgeBatchSmokeResult(empty_contract, 0, 0, False)

    first_contract = validate_bridge_contract(messages[0])
    passed_messages = 0
    for message in messages:
        if validate_bridge_smoke(message).overall_ok:
            passed_messages += 1

    overall_ok = passed_messages == len(messages)
    return BridgeBatchSmokeResult(
        contract=first_contract,
        total_messages=len(messages),
        passed_messages=passed_messages,
        overall_ok=overall_ok,
    )


def build_bridge_message(message_id: int, session_id: int, payload: bytes) -> ZeroCopyFrame:
    message = build_message(message_id, session_id, payload)
    return to_zero_copy(message)
