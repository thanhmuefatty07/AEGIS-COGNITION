from __future__ import annotations

from pathlib import Path

import pytest

from aegis_cognition.application import AgentApplication
from aegis_cognition.config import AgentConfig
from aegis_cognition.observability import RuntimeTelemetry
from aegis_cognition.verification import VerificationSessionError


class _LocalModel:
    requires_api_key = False


def _application(tmp_path: Path, **options: object) -> AgentApplication:
    config = AgentConfig.from_inputs(
        "implement the feature",
        llm=_LocalModel(),
        aese=True,
        aese_project_root=str(tmp_path),
        aese_expected_behavior="the feature returns the documented value",
        **options,
    )
    return AgentApplication(config, telemetry=RuntimeTelemetry())


def test_aese_packet_is_created_before_prompt_construction(tmp_path: Path) -> None:
    application = _application(tmp_path)
    application._ensure_verification_session()
    assert application._verification_packet is not None
    assert application._verification_packet.can_start is True
    context = application._build_system_context(application.config.task)
    assert "AESE IMPLEMENTATION PACKET" in context
    assert application._verification_packet.session_id in context


def test_aese_blocks_source_task_without_explicit_expected_behavior(tmp_path: Path) -> None:
    config = AgentConfig.from_inputs(
        "implement the feature",
        llm=_LocalModel(),
        aese=True,
        aese_project_root=str(tmp_path),
    )
    application = AgentApplication(config, telemetry=RuntimeTelemetry())
    with pytest.raises(VerificationSessionError, match="expected_behavior"):
        application._ensure_verification_session()
