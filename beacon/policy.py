"""Guardrails. These are hard rules, not suggestions to the model.

- No fake reviews, no pretending to be a customer, no undisclosed affiliation.
- Nothing publishes without an approval record that matches the exact current content.
- Regulated businesses (lawyers, notaries) are draft-only, always.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .profile import Profile

REGULATED_DISCLAIMER = "This is general information, not legal advice."

# (pattern, severity, reason). Severity "fail" blocks approval without an override note.
UNIVERSAL_RULES: list[tuple[str, str, str]] = [
    (
        r"\bas an? (happy|satisfied|loyal|long[- ]?time|returning|regular)? ?(customer|client)\b",
        "fail",
        "Written as if by a customer (impersonation).",
    ),
    (
        r"\bI (hired|used|tried|recommend|went to|visited)\b",
        "fail",
        "First-person customer voice (impersonation).",
    ),
    (r"\b([1-5](\.\d)?|five|four)[- ]?stars?\b", "fail", "Star rating - looks like a review."),
    (
        r"\b(testimonial|rave reviews?|customers? (say|love)|clients? (say|love))\b",
        "fail",
        "Review/testimonial language - Beacon never writes reviews.",
    ),
    (r"\bguarantee[ds]?\b|\b100 ?%|\brisk[- ]free\b", "fail", "Guarantee or absolute claim."),
    (
        r"\b(best|#1|number one|no\. ?1|top[- ]rated|leading|most trusted)\b",
        "warn",
        "Superlative - keep only if a fact backs it up.",
    ),
]

REGULATED_RULES: list[tuple[str, str, str]] = [
    (
        r"\b(win|won|winning) (your|the|every) case\b|\bwe (will|always) win\b",
        "fail",
        "Promises an outcome - not allowed for regulated professions.",
    ),
    (
        r"\b(expert|specialist|specializ(e|es|ing) in)\b",
        "warn",
        "Specialisation claims can be restricted for lawyers - confirm with the client.",
    ),
    (
        r"\b(cheapest|lowest (fee|price)s?|discount)\b",
        "warn",
        "Fee advertising can be restricted for lawyers - confirm with the client.",
    ),
]


@dataclass
class Issue:
    rule: str
    severity: str
    reason: str
    excerpt: str

    def as_dict(self) -> dict:
        return asdict(self)


def scan(text: str, profile: Profile) -> list[Issue]:
    rules = list(UNIVERSAL_RULES)
    if profile.regulated:
        rules += REGULATED_RULES
    rules += [
        (re.escape(p), "fail", "Phrase the client asked us never to use.")
        for p in profile.forbidden_phrases
    ]
    issues = []
    for pattern, severity, reason in rules:
        for m in re.finditer(pattern, text, flags=re.I):
            start, end = max(0, m.start() - 30), min(len(text), m.end() + 30)
            issues.append(Issue(pattern, severity, reason, text[start:end].replace("\n", " ")))
            break
    if profile.regulated and REGULATED_DISCLAIMER.lower() not in text.lower():
        issues.append(
            Issue(
                "disclaimer",
                "fail",
                f"Regulated profile: draft must include '{REGULATED_DISCLAIMER}'",
                "",
            )
        )
    return issues


class PolicyError(RuntimeError):
    pass


def check_approval(draft: dict, note: str) -> None:
    if draft["status"] not in ("pending", "rejected"):
        raise PolicyError(f"Draft is {draft['status']}; only pending drafts can be approved.")
    if draft["critic_verdict"] in ("flagged", "unchecked") and not note.strip():
        raise PolicyError(
            "This draft is flagged (or not yet checked). Fix it, or approve with a note "
            "explaining why the flags are acceptable."
        )


def check_publishable(
    profile: Profile, draft: dict, approval: dict | None, current_hash: str
) -> None:
    if profile.regulated:
        raise PolicyError(
            f"{profile.name} is a regulated profile: Beacon never publishes for it. "
            "Hand the approved text to the client to post themselves."
        )
    if draft["status"] != "approved":
        raise PolicyError(f"Draft #{draft['id']} is {draft['status']}, not approved.")
    if approval is None or approval["content_hash"] != current_hash:
        raise PolicyError(f"Draft #{draft['id']} changed after approval; it needs re-approval.")
