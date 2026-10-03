"""Structured-output schemas shared by the content and critic agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DraftItem(BaseModel):
    kind: Literal["faq", "explainer"]
    title: str = Field(
        description="For an FAQ, the customer's question. For an explainer, a title."
    )
    body: str = Field(description="Plain text or light Markdown. Only facts from the fact sheet.")
    fact_ids: list[str] = Field(description="Ids of every fact the body relies on.")


class DraftBatch(BaseModel):
    items: list[DraftItem]


class ClaimCheck(BaseModel):
    claim: str = Field(description="One factual claim made by the draft, quoted or paraphrased.")
    supported: bool = Field(description="True only if the fact sheet directly supports it.")
    fact_ids: list[str] = Field(description="Fact ids that support the claim (empty if none).")
    reason: str


class CriticOutput(BaseModel):
    claims: list[ClaimCheck]
