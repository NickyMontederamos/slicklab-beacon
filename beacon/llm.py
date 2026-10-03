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
    free = False
    supports_web = True

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
    free = True
    supports_web = True

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


# Providers with a free tier that speak the OpenAI chat-completions format.
FREE_PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}


class FreeLLM:
    """Customer-style Q&A on a free-tier model. Only `ask` is supported (no structured output),
    so it is used for AI-visibility runs, never for drafting or fact-checking."""

    free = True
    supports_web = False

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str | None,
        base_url: str,
        delay: float = 3.0,
        http=None,
    ):
        self.name = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.delay = delay
        self._http = http
        self._first = True

    def structured(self, system, prompt, schema, effort="high"):
        raise LLMError("Free models are only used for visibility runs, not drafting or checking.")

    def ask(self, question: str, web_search: bool = False) -> str:
        import time

        import httpx

        if not self._first and self.delay:
            time.sleep(self.delay)
        self._first = False
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": question}],
            "max_tokens": 700,
        }
        client = self._http or httpx.Client(timeout=90)
        last = ""
        for attempt in range(4):
            try:
                r = client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
            except httpx.HTTPError as e:
                raise LLMError(f"Network error: {e}") from e
            if r.status_code == 429 and attempt < 3:  # free tiers rate-limit; wait and retry
                wait = min(60.0, float(r.headers.get("retry-after", 5 * (attempt + 1))))
                time.sleep(wait if self.delay else 0)
                last = "rate limited"
                continue
            if r.status_code >= 400:
                raise LLMError(f"{self.name} error {r.status_code}: {r.text[:200]}")
            try:
                text = r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError) as e:
                raise LLMError(f"Unexpected reply from {self.name}: {r.text[:200]}") from e
            if not (text or "").strip():
                raise LLMError(f"{self.name} returned an empty answer.")
            return text.strip()
        raise LLMError(f"{self.name} kept rate-limiting us ({last}). Try again later.")


def get_visibility_llm(settings: Settings) -> LLM:
    """Which model answers the customer questions. Free tier first; Claude only if chosen."""
    import os

    provider = settings.visibility_provider
    # An explicit free provider wins, even while drafting runs in fake mode.
    if provider in FREE_PROVIDERS:
        base_url, key_env = FREE_PROVIDERS[provider]
        api_key = os.environ.get(key_env) if key_env else None
        if key_env and not api_key:
            raise LLMError(f"Set {key_env} in .env (free key from {provider}).")
        if not settings.free_model:
            raise LLMError(
                "Set BEACON_FREE_MODEL in .env. Run `beacon free-models` to see current "
                "free model names."
            )
        return FreeLLM(provider, settings.free_model, api_key, base_url, settings.free_delay)
    if settings.llm == "fake":
        return FakeLLM()
    if provider == "auto":
        provider = next(
            (p for p, (_, key) in FREE_PROVIDERS.items() if key and os.environ.get(key)), "claude"
        )
    if provider == "claude":
        return ClaudeLLM(settings.model)
    if provider not in FREE_PROVIDERS:
        raise ValueError(
            f"Unknown BEACON_VISIBILITY_PROVIDER {provider!r}. "
            f"Use one of: auto, claude, {', '.join(FREE_PROVIDERS)}."
        )
    base_url, key_env = FREE_PROVIDERS[provider]
    api_key = os.environ.get(key_env) if key_env else None
    if key_env and not api_key:
        raise LLMError(f"Set {key_env} in .env (free key from {provider}).")
    if not settings.free_model:
        raise LLMError(
            "Set BEACON_FREE_MODEL in .env. Run `beacon free-models` to see current "
            "free model names."
        )
    return FreeLLM(provider, settings.free_model, api_key, base_url, settings.free_delay)


def list_free_openrouter_models(http=None) -> list[dict]:
    import httpx

    client = http or httpx.Client(timeout=30)
    r = client.get("https://openrouter.ai/api/v1/models")
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        pr = m.get("pricing") or {}
        if str(pr.get("prompt")) == "0" and str(pr.get("completion")) == "0":
            out.append({"id": m["id"], "context": m.get("context_length")})
    return sorted(out, key=lambda m: -(m["context"] or 0))


def get_llm(settings: Settings) -> LLM:
    if settings.llm == "fake":
        return FakeLLM()
    if settings.llm == "claude":
        return ClaudeLLM(settings.model)
    raise ValueError(f"Unknown BEACON_LLM {settings.llm!r} (use 'claude' or 'fake').")
