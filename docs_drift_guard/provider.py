"""LLM access behind a tiny interface. Offline code paths use MockProvider."""
from __future__ import annotations

import os
from typing import Callable, Protocol


class Provider(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> str: ...


class MockProvider:
    """Deterministic stand-in. Its output is SIMULATED, never model quality."""

    name = "mock"

    def __init__(self, script: Callable[[str], str] | None = None):
        self.script = script
        self.calls: list[str] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append(prompt)
        if self.script:
            return self.script(prompt)
        first = next((ln for ln in prompt.splitlines() if ln.startswith("Change:")), "Change: unknown")
        return f"[simulated, no model was called] Review this section by hand. {first}"


class AnthropicProvider:
    """Optional live provider. Needs `pip install anthropic` and ANTHROPIC_API_KEY."""

    name = "anthropic"

    def __init__(self, client=None, model: str | None = None, max_tokens: int = 1500):
        self.model = model or os.environ.get("DOCS_DRIFT_GUARD_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self._client = client

    def _get_client(self):
        if self._client is None:
            import anthropic  # lazy: only needed in live mode

            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        msg = self._get_client().messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content).strip()


def make_provider(kind: str) -> Provider | None:
    if kind == "none":
        return None
    if kind == "mock":
        return MockProvider()
    if kind == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"unknown provider: {kind}")
