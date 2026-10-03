"""LLM access. `ClaudeLLM` talks to the Anthropic API; `FakeLLM` is offline and deterministic."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .config import Settings

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallback: if the main model declines, the API retries on a
# model chosen by refusal category inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str
    model: str

    def structured(self, system: str, prompt: str, schema: type[T], effort: str = "high") -> T: ...

    def ask(self, question: str, web_search: bool = False) -> str: ...


class ClaudeLLM:
    name = "claude"

    def __init__(self, model: str):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=3)
        self.model = model

    def _check(self, response) -> None:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"Model declined the request (category: {category}).")
        if response.stop_reason == "max_tokens":
            raise LLMError("Response hit max_tokens before finishing.")

    def structured(self, system: str, prompt: str, schema: type[T], effort: str = "high") -> T:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Network error: {e}") from e
        self._check(response)
        if response.parsed_output is None:
            raise LLMError("Model returned no structured output.")
        return response.parsed_output

    def ask(self, question: str, web_search: bool = False) -> str:
        """Ask the way a customer would: no system prompt, no hints about the business."""
        messages: list[dict] = [{"role": "user", "content": question}]
        kwargs: dict = {}
        if web_search:
            kwargs["tools"] = [WEB_SEARCH_TOOL]
        try:
            for _ in range(4):  # resume server-tool turns that pause
                response = self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    messages=messages,
                    output_config={"effort": "medium"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                    **kwargs,
                )
                if response.stop_reason != "pause_turn":
                    break
                messages.append({"role": "assistant", "content": response.content})
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Network error: {e}") from e
        if response.stop_reason == "refusal":
            raise LLMError("Model declined the question.")
        return "\n".join(b.text for b in response.content if b.type == "text").strip()


class FakeLLM:
    """Deterministic stand-in for demos and tests. Never claims to know the business."""

    name = "fake"
    model = "fake-1"

    def __init__(self, mention_names: list[str] | None = None, mention_every: int = 0):
        self.mention_names = mention_names or []
        self.mention_every = mention_every
        self._calls = 0

    def structured(self, system: str, prompt: str, schema: type[T], effort: str = "high") -> T:
        from .agents.schemas import ClaimCheck, CriticOutput, DraftBatch, DraftItem

        facts = re.findall(r"^\[([\w-]+)\] (.+)$", prompt, flags=re.M)
        if schema is DraftBatch:
            items = []
            for fid, text in facts[:3]:
                items.append(
                    DraftItem(
                        kind="faq",
                        title=f"What should I know about this? ({fid})",
                        body=text,
                        fact_ids=[fid],
                    )
                )
            return schema(items=items)  # type: ignore[return-value]
        if schema is CriticOutput:
            draft = prompt.split("DRAFT:", 1)[-1]
            claims = []
            lines = [s.strip() for s in re.split(r"[.\n]", draft) if s.strip()]
            for line in [s for s in lines if not s.endswith("?") and "?" not in s][:5]:
                hit = next((fid for fid, text in facts if line.lower() in text.lower()), None)
                claims.append(
                    ClaimCheck(
                        claim=line,
                        supported=hit is not None,
                        fact_ids=[hit] if hit else [],
                        reason="Matches a fact." if hit else "No fact states this.",
                    )
                )
            return schema(claims=claims)  # type: ignore[return-value]
        raise LLMError(f"FakeLLM cannot produce {schema.__name__}")

    def ask(self, question: str, web_search: bool = False) -> str:
        self._calls += 1
        digest = hashlib.sha256(question.encode()).hexdigest()[:6]
        base = f"Here are some general options to consider ({digest}). Check reviews and compare."
        if self.mention_names and self.mention_every and self._calls % self.mention_every == 0:
            return f"{base} One option is {self.mention_names[0]}."
        return base


def get_llm(settings: Settings) -> LLM:
    if settings.llm == "fake":
        return FakeLLM()
    if settings.llm == "claude":
        return ClaudeLLM(settings.model)
    raise ValueError(f"Unknown BEACON_LLM {settings.llm!r} (use 'claude' or 'fake').")
