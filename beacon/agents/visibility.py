"""Visibility baseline: ask AI assistants realistic customer questions, repeatedly, and record
how often the business is named. Re-run after changes and compare.

Answers vary run to run, so each question is asked several times and the comparison reports
whether a change is bigger than chance (two-proportion z-test), not just the raw difference.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable

from ..llm import LLM, LLMError
from ..normalize import mentions, phone_digits, phones_in
from ..profile import Profile
from ..store import Store


def identifiers(profile: Profile) -> tuple[list[str], list[str]]:
    extra = []
    if profile.website:
        domain = profile.website.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
        extra.append(domain)
    return profile.all_names, extra


def detect(profile: Profile, answer: str) -> list[str]:
    names, extra = identifiers(profile)
    found = mentions(answer, names, extra)
    if profile.contact.phone and phone_digits(profile.contact.phone) in phones_in(answer):
        found.append("phone")
    return found


def run(
    profile: Profile,
    llm: LLM,
    store: Store,
    label: str,
    runs: int | None = None,
    web_search: bool = False,
    progress: Callable[[str], None] | None = None,
) -> int:
    questions = profile.visibility.questions
    if not questions:
        raise ValueError("Profile has no visibility.questions.")
    runs = runs or profile.visibility.runs_per_question
    web_search = web_search and getattr(llm, "supports_web", True)
    provider = f"{llm.name}{'+web' if web_search else ''}"
    run_id = store.start_visibility_run(label, provider, llm.model, runs)
    store.log("visibility-agent", "visibility.start", {"run_id": run_id, "label": label})
    try:
        for q in questions:
            for attempt in range(1, runs + 1):
                try:
                    answer = llm.ask(q, web_search=web_search)
                    found = detect(profile, answer)
                    store.add_answer(run_id, q, attempt, answer, bool(found), found)
                except LLMError as e:
                    store.add_answer(run_id, q, attempt, "", False, [], error=str(e))
                if progress:
                    progress(f"{q[:60]} [{attempt}/{runs}]")
    except Exception:
        store.finish_visibility_run(run_id, "failed")
        raise
    store.finish_visibility_run(run_id, "done")
    store.log("visibility-agent", "visibility.done", {"run_id": run_id})
    return run_id


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(store: Store, run_id: int) -> dict:
    answers = [a for a in store.answers(run_id) if a["error"] is None]
    by_q: dict[str, list[int]] = defaultdict(list)
    for a in answers:
        by_q[a["question"]].append(a["mentioned"])
    k, n = sum(a["mentioned"] for a in answers), len(answers)
    errors = sum(1 for a in store.answers(run_id) if a["error"])
    return {
        "run": store.visibility_run(run_id),
        "mentions": k,
        "total": n,
        "errors": errors,
        "rate": k / n if n else 0.0,
        "ci": wilson(k, n),
        "per_question": [
            {"question": q, "mentions": sum(v), "total": len(v), "rate": sum(v) / len(v)}
            for q, v in by_q.items()
        ],
    }


def compare(store: Store, before_id: int, after_id: int) -> dict:
    a, b = summarize(store, before_id), summarize(store, after_id)
    p_value = two_proportion_p(a["mentions"], a["total"], b["mentions"], b["total"])
    before_q = {q["question"]: q for q in a["per_question"]}
    rows = []
    for q in b["per_question"]:
        prev = before_q.get(q["question"])
        rows.append(
            {
                "question": q["question"],
                "before": prev["rate"] if prev else None,
                "after": q["rate"],
                "delta": q["rate"] - prev["rate"] if prev else None,
            }
        )
    if p_value is None:
        verdict = "Not enough answers to compare."
    elif p_value < 0.05:
        verdict = "improved" if b["rate"] > a["rate"] else "worse"
        verdict = f"Real change: visibility {verdict} (p={p_value:.3f})."
    else:
        verdict = f"No clear change yet (p={p_value:.2f}); could be run-to-run noise."
    ra, rb = a["run"], b["run"]
    same = (ra["provider"], ra["model"]) == (rb["provider"], rb["model"])
    warning = None
    if not same:
        warning = (
            f"These runs used different models ({ra['provider']}/{ra['model']} vs "
            f"{rb['provider']}/{rb['model']}), so the difference may not come from your "
            "changes. Re-run with the same model to compare fairly."
        )
    return {
        "before": a,
        "after": b,
        "delta": b["rate"] - a["rate"],
        "p_value": p_value,
        "verdict": verdict,
        "questions": rows,
        "warning": warning,
    }


def two_proportion_p(k1: int, n1: int, k2: int, n2: int) -> float | None:
    if n1 == 0 or n2 == 0:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(k1 / n1 - k2 / n2) / se
    return math.erfc(z / math.sqrt(2))
