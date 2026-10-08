import json
import re

import pytest

from core.python.aegis.connection_clients import (
    AnthropicMessagesClient,
    ConnectionClientError,
    ConnectionResponse,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    ProviderToolDefinition,
    ProviderToolExchange,
    ProviderToolOutput,
    XaiResponsesClient,
    provider_tool_alias,
)
from core.python.aegis.connections import ConnectionRecord


def _connection(protocol: str) -> ConnectionRecord:
    provider, endpoint = {
        "chat-completions": ("openai-compatible", "http://127.0.0.1:8080/v1"),
        "xai-responses": ("xai", "https://api.x.ai/v1"),
        "openai-responses": ("openai", "https://api.openai.com/v1"),
        "anthropic-messages": ("anthropic", "https://api.anthropic.com/v1"),
    }[protocol]
    return ConnectionRecord(
        connection_id=f"test-{provider}",
        provider_kind=provider,
        endpoint=endpoint,
        protocol=protocol,
        secret_ref=None,
        enabled=True,
        revision=1,
        updated_at_ms=1,
    )


def _tool() -> ProviderToolDefinition:
    return ProviderToolDefinition(
        name="mcp.research.search",
        description="Search approved public sources for evidence.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )


def _requester(responses: list[dict[str, object]], requests: list[dict[str, object]]):
    def request(url: str, _headers: object, body: bytes, _timeout: float) -> ConnectionResponse:
        requests.append({"url": url, "payload": json.loads(body)})
        return ConnectionResponse(200, json.dumps(responses.pop(0)).encode("utf-8"))

    return request


def _client(protocol: str, requester: object):
    connection = _connection(protocol)
    shared = {"requester": requester, "egress_check": lambda _connection_id: True}
    if protocol == "chat-completions":
        return OpenAICompatibleClient(connection, model="test-model", **shared)
    if protocol == "xai-responses":
        return XaiResponsesClient(connection, model="grok-test", **shared)
    if protocol == "openai-responses":
        return OpenAIResponsesClient(connection, model="gpt-test", **shared)
    return AnthropicMessagesClient(connection, model="claude-test", **shared)


def test_tool_alias_is_stable_and_uses_a_provider_safe_name():
    alias = provider_tool_alias("mcp.research.search")

    assert alias == provider_tool_alias("mcp.research.search")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)
    assert alias != "mcp.research.search"


@pytest.mark.parametrize("protocol", ["chat-completions", "xai-responses", "openai-responses", "anthropic-messages"])
def test_tool_call_turn_and_provider_specific_history_round_trip(protocol: str):
    tool = _tool()
    alias = provider_tool_alias(tool.name)
    if protocol == "chat-completions":
        first = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": alias, "arguments": '{"query":"rust"}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        second = {"choices": [{"message": {"role": "assistant", "content": "Evidence found."}, "finish_reason": "stop"}]}
    elif protocol == "anthropic-messages":
        first = {
            "content": [{"type": "text", "text": "I will search."}, {"type": "tool_use", "id": "toolu-1", "name": alias, "input": {"query": "rust"}}],
            "stop_reason": "tool_use",
        }
        second = {"content": [{"type": "text", "text": "Evidence found."}], "stop_reason": "end_turn"}
    else:
        first = {
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": alias,
                    "arguments": '{"query":"rust"}',
                },
            ],
            "status": "completed",
        }
        second = {
            "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Evidence found."}]}],
            "status": "completed",
        }
    requests: list[dict[str, object]] = []
    client = _client(protocol, _requester([first, second], requests))

    turn = client.invoke_turn("Find evidence", tools=(tool,))

    assert turn.text == "I will search." if protocol == "anthropic-messages" else turn.text == ""
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == tool.name
    assert turn.tool_calls[0].arguments == {"query": "rust"}
    exchange = ProviderToolExchange(
        assistant_state=turn.assistant_state,
        outputs=(ProviderToolOutput(turn.tool_calls[0].call_id, '{"items":["source"]}'),),
    )

    final = client.invoke_turn("Find evidence", tools=(tool,), tool_history=(exchange,))

    assert final.text == "Evidence found."
    first_payload = requests[0]["payload"]
    second_payload = requests[1]["payload"]
    if protocol == "chat-completions":
        assert first_payload["tools"][0]["function"]["name"] == alias
        assert second_payload["messages"][-2] == turn.assistant_state
        assert second_payload["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": '{"items":["source"]}',
        }
    elif protocol == "anthropic-messages":
        assert first_payload["tools"][0]["name"] == alias
        assert second_payload["messages"][-2] == {"role": "assistant", "content": turn.assistant_state}
        assert second_payload["messages"][-1]["content"] == [
            {"type": "tool_result", "tool_use_id": "toolu-1", "content": '{"items":["source"]}'}
        ]
    else:
        assert first_payload["tools"][0]["name"] == alias
        assert second_payload["input"][-2] == turn.assistant_state[-1]
        assert second_payload["input"][-1] == {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": '{"items":["source"]}',
        }
        assert second_payload["store"] is False
        assert "previous_response_id" not in second_payload


def test_provider_rejects_malformed_tool_arguments_before_returning_a_call():
    alias = provider_tool_alias(_tool().name)
    client = _client(
        "chat-completions",
        _requester(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": alias, "arguments": '{"query":NaN}'},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
            [],
        ),
    )

    with pytest.raises(ConnectionClientError, match="not valid JSON"):
        client.invoke_turn("Search", tools=(_tool(),))


def test_text_only_invoke_does_not_discard_a_provider_tool_call():
    alias = provider_tool_alias(_tool().name)
    client = _client(
        "chat-completions",
        _requester(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": alias, "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
            [],
        ),
    )

    with pytest.raises(ConnectionClientError, match="use invoke_turn"):
        client.invoke("Search", tools=(_tool(),))
