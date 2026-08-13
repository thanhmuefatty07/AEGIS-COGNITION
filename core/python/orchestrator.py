from dataclasses import dataclass


@dataclass(frozen=True)
class MessageIdentity:
    message_id: int
    session_id: int


@dataclass(frozen=True)
class MessageFrame:
    message_id: int
    session_id: int
    payload: bytes

    @property
    def payload_len(self) -> int:
        return len(self.payload)

    def is_valid(self) -> bool:
        return self.message_id > 0 and self.session_id > 0 and self.payload_len > 0


@dataclass(frozen=True)
class ZeroCopyFrame:
    message_id: int
    session_id: int
    payload_len: int

    def is_valid(self) -> bool:
        return self.message_id > 0 and self.session_id > 0 and self.payload_len > 0


def build_message(message_id: int, session_id: int, payload: bytes) -> MessageFrame:
    return MessageFrame(message_id=message_id, session_id=session_id, payload=payload)


def to_zero_copy(message: MessageFrame) -> ZeroCopyFrame:
    if not message.is_valid():
        raise ValueError("invalid message frame")
    return ZeroCopyFrame(
        message_id=message.message_id,
        session_id=message.session_id,
        payload_len=message.payload_len,
    )


def validate_runtime_message(message: MessageFrame) -> bool:
    return message.is_valid()


def message_identity(message: MessageFrame) -> MessageIdentity:
    return MessageIdentity(message_id=message.message_id, session_id=message.session_id)


def generate_skeleton(code: str) -> str:
    try:
        import aegis_nerve
        return aegis_nerve.aegis_harness_generate_skeleton(code)
    except ImportError:
        # Simple regex fallback to strip implementation bodies
        lines = []
        in_fn = False
        brace_count = 0
        for line in code.splitlines():
            if 'fn ' in line:
                in_fn = True
                brace_count = 0
                parts = line.split('{')
                lines.append(parts[0] + '{ todo!() }')
            elif in_fn:
                brace_count += line.count('{') - line.count('}')
                if brace_count <= 0:
                    in_fn = False
            else:
                lines.append(line)
        return '\n'.join(lines)


def analyze_compile_errors(logs: str) -> list[str]:
    try:
        import aegis_nerve
        return aegis_nerve.aegis_harness_analyze_errors(logs)
    except ImportError:
        errors = []
        for line in logs.splitlines():
            if line.strip().startswith("error[") or line.strip().startswith("error:"):
                errors.append(line.strip())
        return errors
