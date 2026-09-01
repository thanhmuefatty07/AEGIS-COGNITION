"""
NVIDIA NIM (NVAPI) client for AEGIS-COGNITION.

Uses the OpenAI-compatible endpoint provided by NVIDIA NIM to interface
with moonshotai/kimi-k2.6 (and other NIM-hosted models).

Usage:
    from core.python.nim_client import NIMClient, NIMConfig

    client = NIMClient.from_env()
    response = client.chat("Explain the AEGIS-COGNITION bridge contract.")
    print(response)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any
from collections.abc import Generator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NIM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
NIM_DEFAULT_MODEL: str = "moonshotai/kimi-k2.6"


@dataclass
class NIMConfig:
    """Immutable configuration for an NVIDIA NIM session."""

    api_key: str
    base_url: str = NIM_BASE_URL
    model: str = NIM_DEFAULT_MODEL
    temperature: float = 0.6
    top_p: float = 0.7
    max_tokens: int = 4096
    stream: bool = False

    # -----------------------------------------------------------------------
    # Factory helpers
    # -----------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> NIMConfig:
        """
        Build a NIMConfig from environment variables.

        Required env vars:
            NVIDIA_NIM_API_KEY  - your nvapi-… key

        Optional env vars (fall back to defaults above):
            NVIDIA_NIM_BASE_URL
            NVIDIA_NIM_MODEL
        """
        api_key = os.environ.get("NVIDIA_NIM_API_KEY", "")
        if not api_key:
            raise OSError(
                "NVIDIA_NIM_API_KEY is not set. "
                "Add it to your .env file or export it before running."
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("NVIDIA_NIM_BASE_URL", NIM_BASE_URL),
            model=os.environ.get("NVIDIA_NIM_MODEL", NIM_DEFAULT_MODEL),
        )


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    """A single turn in a conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class NIMResponse:
    """Parsed response from the NIM endpoint."""

    content: str
    model: str
    usage_prompt_tokens: int
    usage_completion_tokens: int
    usage_total_tokens: int
    finish_reason: str

    def __str__(self) -> str:
        return self.content


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class NIMClient:
    """
    Thin wrapper around the NVIDIA NIM OpenAI-compatible REST API.

    The openai package is used as the transport layer because NIM exposes
    an OpenAI-compatible endpoint.
    """

    def __init__(self, config: NIMConfig) -> None:
        self._config = config
        self._client = self._build_client()

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def _build_client(self):
        """Lazily import and configure the openai client."""
        try:
            from openai import OpenAI  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for NIMClient. "
                "Install it with:  pip install openai"
            ) from exc

        return OpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
        )

    # -----------------------------------------------------------------------
    # Factory
    # -----------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> NIMClient:
        """Convenience factory that reads config from environment variables."""
        return cls(NIMConfig.from_env())

    # -----------------------------------------------------------------------
    # Core chat interface
    # -----------------------------------------------------------------------

    def chat(
        self,
        prompt: str,
        *,
        system: str = "You are a helpful AI assistant integrated into AEGIS-COGNITION.",
        history: list[ChatMessage] | None = None,
    ) -> NIMResponse:
        """
        Send a single-turn or multi-turn chat request.

        Args:
            prompt:  The user message.
            system:  Optional system prompt override.
            history: Optional list of prior ChatMessages for multi-turn context.

        Returns:
            NIMResponse with the assistant's reply and usage metadata.
        """
        messages = [ChatMessage(role="system", content=system)]
        if history:
            messages.extend(history)
        messages.append(ChatMessage(role="user", content=prompt))

        raw = self._client.chat.completions.create(
            model=self._config.model,
            messages=[m.to_dict() for m in messages],
            temperature=self._config.temperature,
            top_p=self._config.top_p,
            max_tokens=self._config.max_tokens,
            stream=False,
        )

        choice = raw.choices[0]
        usage = raw.usage

        return NIMResponse(
            content=choice.message.content or "",
            model=raw.model,
            usage_prompt_tokens=usage.prompt_tokens,
            usage_completion_tokens=usage.completion_tokens,
            usage_total_tokens=usage.total_tokens,
            finish_reason=choice.finish_reason or "unknown",
        )

    def stream_chat(
        self,
        prompt: str,
        *,
        system: str = "You are a helpful AI assistant integrated into AEGIS-COGNITION.",
        history: list[ChatMessage] | None = None,
    ) -> Generator[str]:
        """
        Stream a chat response token-by-token.

        Yields:
            Partial content strings as they arrive from the API.

        Example::
            for chunk in client.stream_chat("Describe the memory bridge."):
                print(chunk, end="", flush=True)
        """
        messages = [ChatMessage(role="system", content=system)]
        if history:
            messages.extend(history)
        messages.append(ChatMessage(role="user", content=prompt))

        stream = self._client.chat.completions.create(
            model=self._config.model,
            messages=[m.to_dict() for m in messages],
            temperature=self._config.temperature,
            top_p=self._config.top_p,
            max_tokens=self._config.max_tokens,
            stream=True,
        )

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    # -----------------------------------------------------------------------
    # Convenience helpers
    # -----------------------------------------------------------------------

    def invoke(self, prompt: str, **kwargs: Any) -> str:
        """LangChain-compatible sync invoke method."""
        system = kwargs.pop("system", "You are a helpful AI assistant integrated into AEGIS-COGNITION.")
        if "system_context" in kwargs:
            system = kwargs.pop("system_context")
        return self.chat(prompt, system=system, **kwargs).content

    async def ainvoke(self, prompt: str, **kwargs: Any) -> str:
        """LangChain-compatible async ainvoke method."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.invoke(prompt, **kwargs))

    def complete(self, prompt: str) -> str:
        """Shorthand that returns only the assistant text string."""
        return self.chat(prompt).content

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def __repr__(self) -> str:
        return f"NIMClient(model={self.model!r}, base_url={self.base_url!r})"
