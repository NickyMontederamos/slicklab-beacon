"""Content agent: drafts FAQs and explainers using only the profile's facts.

Every draft lands in the queue as 'pending' and goes straight through the critic.
"""

from __future__ import annotations

from ..llm import LLM
from ..policy import REGULATED_DISCLAIMER
from ..profile import Profile
from ..store import Store
from .critic import review_draft
from .schemas import DraftBatch

CONTENT_SYSTEM = """You write website copy for a real local business.
Hard rules:
- Use ONLY facts from the FACT SHEET. Do not add numbers, prices, years, credentials, awards,
  outcomes, or comparisons that are not stated there. If a useful answer needs a fact that is
  missing, leave it out.
- List in fact_ids every fact id the text relies on.
- Never write reviews, testimonials, star ratings, or anything in a customer's voice.
- Never claim to be a customer or an independent third party. Write as the business ("we")
  or neutrally about the business.
- No guarantees, no "best"/"#1"/"leading" unless a fact says so.
- FAQ titles are real questions a customer would type into a search box or ask an AI assistant.
- Keep answers short: 2-5 sentences, plain language."""

REGULATED_EXTRA = f"""
This is a regulated profession. Additionally:
- Never promise or imply outcomes.
- No self-praise, no claims of being a specialist or expert unless a fact states it verbatim.
- End every body with exactly: "{REGULATED_DISCLAIMER}" """


def generate(
    profile: Profile,
    llm: LLM,
    store: Store,
    count: int = 5,
    kind: str = "faq",
    topic: str | None = None,
    actor: str = "content-agent",
) -> list[int]:
    system = CONTENT_SYSTEM + (REGULATED_EXTRA if profile.regulated else "")
    ask = (
        f"Business: {profile.name} ({profile.category}).\nTone: {profile.tone}\n\n"
        f"FACT SHEET:\n{profile.fact_text()}\n\n"
        f"Write {count} {'FAQ entries' if kind == 'faq' else 'short explainers'} (kind={kind})."
    )
    if topic:
        ask += f"\nFocus on: {topic}"
    existing = [d["title"] for d in store.list_drafts() if d["status"] != "rejected"]
    if existing:
        ask += "\nDo not repeat these existing titles:\n" + "\n".join(f"- {t}" for t in existing)

    batch = llm.structured(system, ask, DraftBatch, effort="high")
    valid_ids = {f.id for f in profile.fact_sheet()}
    ids = []
    for item in batch.items[:count]:
        body = item.body.strip()
        if profile.regulated and not body.endswith(REGULATED_DISCLAIMER):
            body = f"{body}\n\n{REGULATED_DISCLAIMER}"
        fact_ids = [f for f in item.fact_ids if f in valid_ids]
        draft_id = store.add_draft(item.kind, item.title.strip(), body, fact_ids, actor)
        review_draft(profile, store, draft_id, llm)
        ids.append(draft_id)
    store.log(actor, "content.generate", {"draft_ids": ids, "kind": kind, "topic": topic})
    return ids
