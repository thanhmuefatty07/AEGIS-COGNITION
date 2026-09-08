"""Safe, local-only client for the optional ChatGPT Web Responses bridge.

The bridge is an external, user-authenticated browser process.  AEGIS only speaks
to its loopback Responses endpoint; it never reads browser cookies or session
storage and never treats the local placeholder key as a secret.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


CHATGPT_WEB_BASE_URL = "http://127.0.0.1:17841/v1"
CHATGPT_WEB_DEFAULT_MODEL = "chatgpt-web/high"
_LOCAL_API_KEY = "local-loopback"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class ChatGPTWebUnavailableError(RuntimeError):
    """Raised when the local bridge is not reachable or not configured."""


@dataclass(frozen=True)
class ChatGPTWebConfig:
    """Validated configuration for a loopback-only bridge connection."""

    base_url: str = CHATGPT_WEB_BASE_URL
    model: str = CHATGPT_WEB_DEFAULT_MODEL
    timeout: float = 120.0

    def __post_init__(self) -> None:
        normalized = _normalize_base_url(self.base_url)
        if not self.model.startswith("chatgpt-web/"):
            raise ValueError("ChatGPT Web model must use the chatgpt-web/ prefix")
        if not self.model.split("/", 1)[1].strip():
            raise ValueError("ChatGPT Web model name is required")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool):
            raise ValueError("ChatGPT Web timeout must be numeric")
        if not 1.0 <= float(self.timeout) <= 3_600.0:
            raise ValueError("ChatGPT Web timeout must be between 1 and 3600 seconds")
        object.__setattr__(self, "base_url", normalized)
        object.__setattr__(self, "timeout", float(self.timeout))

    @classmethod
    def from_env(cls) -> ChatGPTWebConfig:
        """Read only non-secret routing settings from the environment."""

        raw_timeout = os.environ.get("AEGIS_CHATGPT_WEB_TIMEOUT", "120")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("AEGIS_CHATGPT_WEB_TIMEOUT must be numeric") from exc
        return cls(
            base_url=os.environ.get("AEGIS_CHATGPT_WEB_BASE_URL", CHATGPT_WEB_BASE_URL),
            model=os.environ.get("AEGIS_CHATGPT_WEB_MODEL", CHATGPT_WEB_DEFAULT_MODEL),
            timeout=timeout,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ChatGPTWebConfig:
        """Build configuration from ``[llm]`` in ``~/.aegis/config.toml``."""

        timeout = value.get("timeout", 120.0)
        return cls(
            base_url=str(value.get("base_url", CHATGPT_WEB_BASE_URL)),
            model=str(value.get("model", CHATGPT_WEB_DEFAULT_MODEL)),
            timeout=float(timeout),
        )


def _normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ChatGPT Web base_url is required")
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("ChatGPT Web base_url must be an http loopback URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ChatGPT Web base_url must not contain credentials, query, or fragment")
    if parsed.path != "/v1":
        raise ValueError("ChatGPT Web base_url must end with /v1")
    return candidate


class ChatGPTWebClient:
    """LangChain-compatible text client backed by the local Responses bridge."""

    requires_api_key = False

    def __init__(self, config: ChatGPTWebConfig | None = None, *, client: Any = None) -> None:
        self._config = config or ChatGPTWebConfig.from_env()
        self._client = client

    @classmethod
    def from_env(cls) -> ChatGPTWebClient:
        return cls(ChatGPTWebConfig.from_env())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ChatGPTWebClient:
        return cls(ChatGPTWebConfig.from_mapping(value))

    def _openai_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ImportError("The 'openai' package is required for ChatGPTWebClient") from exc
            # The bridge is local-only and does not validate an API credential.  A
            # non-secret placeholder is required by the OpenAI SDK constructor.
            self._client = OpenAI(
                base_url=self._config.base_url,
                api_key=_LOCAL_API_KEY,
                timeout=self._config.timeout,
            )
        return self._client

    def health(self) -> dict[str, Any]:
        """Return the bridge health document without sending a model request."""

        request = Request(f"{self._config.base_url[:-3]}/healthz", method="GET")
        try:
            with urlopen(request, timeout=min(self._config.timeout, 10.0)) as response:
                import json

                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError) as exc:
            raise ChatGPTWebUnavailableError(
                "ChatGPT Web bridge is unavailable; start codex-chatgpt-web and complete sign-in"
            ) from exc
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ChatGPTWebUnavailableError("ChatGPT Web bridge returned an invalid health document")
        return payload

    def invoke(self, prompt: str, **kwargs: Any) -> str:
        """Run one text turn through the selected account-eligible Web model."""

        instructions = kwargs.pop("instructions", kwargs.pop("system_context", None))
        allowed = {"max_output_tokens", "temperature", "top_p", "reasoning", "previous_response_id"}
        request_options = {key: kwargs.pop(key) for key in tuple(kwargs) if key in allowed}
        # AEGIS orchestration metadata (for example ``mode``) is not a Responses
        # field.  It must not leak into the external request or fail a compatible
        # provider call.
        kwargs.clear()
        options: dict[str, Any] = {
            "model": self._config.model,
            "input": prompt,
            **request_options,
        }
        if instructions is not None:
            options["instructions"] = str(instructions)
        try:
            response = self._openai_client().responses.create(**options)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429:
                raise
            raise ChatGPTWebUnavailableError(
                "ChatGPT Web bridge request failed; check `codex-chatgpt-web doctor` and account limits"
            ) from exc
        text = _response_text(response)
        if not text:
            raise ChatGPTWebUnavailableError("ChatGPT Web bridge returned no output text")
        return text

    async def ainvoke(self, prompt: str, **kwargs: Any) -> str:
        """Async compatibility wrapper used by the AEGIS provider router."""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.invoke(prompt, **kwargs))

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def __repr__(self) -> str:
        return f"ChatGPTWebClient(model={self.model!r}, base_url={self.base_url!r})"


def _response_text(response: Any) -> str:
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    output = response.get("output") if isinstance(response, Mapping) else getattr(response, "output", None)
    if not isinstance(output, (list, tuple)):
        return ""
    parts: list[str] = []
    for item in output:
        content = item.get("content") if isinstance(item, Mapping) else getattr(item, "content", None)
        if not isinstance(content, (list, tuple)):
            continue
        for block in content:
            text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


__all__ = [
    "CHATGPT_WEB_BASE_URL",
    "CHATGPT_WEB_DEFAULT_MODEL",
    "ChatGPTWebClient",
    "ChatGPTWebConfig",
    "ChatGPTWebUnavailableError",
]
