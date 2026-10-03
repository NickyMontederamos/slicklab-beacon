"""Critic agent: checks every claim in a draft against the profile's facts.

Two layers:
1. Deterministic - policy rules (reviews, impersonation, guarantees, regulated rules) and
   hard facts (phones, emails, URLs, money, percentages, years) that must appear in the fact sheet.
2. LLM - lists each factual claim and marks whether a fact supports it.
"""

from __future__ import annotations

import re

from ..llm import LLM, LLMError
from ..normalize import fold, phones_in
from ..policy import scan
from ..profile import Profile
from ..store import Store
from .schemas import CriticOutput

CRITIC_SYSTEM = """You are a strict fact-checker for marketing copy about a real business.
You get a FACT SHEET (each fact has an id in brackets) and a DRAFT.
List every factual claim the draft makes about the business: who they are, where, when they
are open, what they offer, prices, numbers, credentials, results, comparisons.
A claim is supported only if a fact directly states it. Reasonable paraphrase is fine; inference,
generalisation, or anything extra (even if probably true) is NOT supported.
Generic advice that says nothing about the business is not a claim - skip it."""

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RE = re.compile(r"https?://[^\s)>\]]+|\b[\w-]+\.(?:com|digital|ph|net|org|io)\b[^\s)]*", re.I)
MONEY_RE = re.compile(r"(?<![A-Za-z])(?:₱|PHP|Php|P|\$|USD)\s?\d[\d,]*(?:\.\d+)?")
PCT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def hard_fact_issues(text: str, profile: Profile) -> list[dict]:
    facts = profile.fact_text()
    ffold = fold(facts)
    fphones = phones_in(facts)
    out = []
    for p in phones_in(text):
        if p not in fphones:
            out.append({"kind": "phone", "value": p, "reason": "Phone number not in fact sheet."})
    for e in EMAIL_RE.findall(text):
        if fold(e) not in ffold:
            out.append({"kind": "email", "value": e, "reason": "Email not in fact sheet."})
    for u in URL_RE.findall(text):
        bare = fold(u).removeprefix("https://").removeprefix("http://").rstrip("/.,")
        if bare not in ffold:
            out.append({"kind": "url", "value": u, "reason": "Link not in fact sheet."})
    for regex, kind in ((MONEY_RE, "price"), (PCT_RE, "percentage"), (YEAR_RE, "year")):
        for v in regex.findall(text):
            if fold(v).replace(" ", "") not in ffold.replace(" ", ""):
                out.append(
                    {"kind": kind, "value": v, "reason": f"{kind.title()} not in fact sheet."}
                )
    return out


def review(profile: Profile, title: str, body: str, llm: LLM | None) -> tuple[dict, str]:
    text = f"{title}\n{body}"
    policy = [i.as_dict() for i in scan(text, profile)]
    hard = hard_fact_issues(text, profile)
    claims: list[dict] = []
    llm_error = None
    if llm is not None:
        try:
            out = llm.structured(
                CRITIC_SYSTEM,
                f"FACT SHEET:\n{profile.fact_text()}\n\nDRAFT:\n{title}\n\n{body}",
                CriticOutput,
                effort="high",
            )
            valid_ids = {f.id for f in profile.fact_sheet()}
            for c in out.claims:
                d = c.model_dump()
                bad_ids = [i for i in d["fact_ids"] if i not in valid_ids]
                if d["supported"] and (bad_ids or not d["fact_ids"]):
                    d["supported"] = False
                    d["reason"] += " (cited fact id missing or unknown)"
                claims.append(d)
        except LLMError as e:
            llm_error = str(e)

    unsupported = [c for c in claims if not c["supported"]]
    fails = [i for i in policy if i["severity"] == "fail"]
    warns = [i for i in policy if i["severity"] == "warn"]
    if fails or hard or unsupported:
        verdict = "flagged"
    elif llm is None or llm_error:
        verdict = "unchecked"
    elif warns:
        verdict = "warn"
    else:
        verdict = "clean"
    report = {
        "policy": policy,
        "hard_facts": hard,
        "claims": claims,
        "llm": getattr(llm, "model", None),
        "llm_error": llm_error,
    }
    return report, verdict


def review_draft(profile: Profile, store: Store, draft_id: int, llm: LLM | None) -> str:
    d = store.get_draft(draft_id)
    if d is None:
        raise KeyError(draft_id)
    report, verdict = review(profile, d["title"], d["body"], llm)
    store.set_critic(draft_id, report, verdict)
    store.log("critic", "critic.review", {"draft_id": draft_id, "verdict": verdict})
    return verdict
