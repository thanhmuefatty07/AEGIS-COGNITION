from __future__ import annotations

from aegis_cognition.application import AgentApplication
from aegis_cognition.config import AgentConfig


class LocalModel:
    requires_api_key = False


def test_cache_first_system_context_excludes_the_changing_task() -> None:
    application = AgentApplication(AgentConfig.from_inputs("first task", llm=LocalModel()))

    first = application._build_system_context("first task", cache_stable=True)
    second = application._build_system_context("second task", cache_stable=True)

    assert first == second
    assert "first task" not in first
    assert "second task" not in second
    assert "active objective" in first


def test_prepare_keeps_the_task_in_the_dynamic_user_side() -> None:
    application = AgentApplication(AgentConfig.from_inputs("first task", llm=LocalModel()))

    formatted_task, system_context = application.prepare("first task")

    assert formatted_task.startswith("first task")
    assert "first task" not in system_context
