import pytest

from beacon.agents import content, critic
from beacon.llm import FakeLLM
from beacon.policy import REGULATED_DISCLAIMER, PolicyError, check_approval, scan


def test_review_language_is_blocked(profile):
    issues = scan("As a happy customer I hired them. Five stars!", profile)
    assert any(i.severity == "fail" for i in issues)


def test_regulated_needs_disclaimer_and_no_outcome_promises(regulated):
    reasons = [i.reason for i in scan("We will win your case.", regulated)]
    assert any("outcome" in r for r in reasons)
    assert any("disclaimer" in r.lower() or REGULATED_DISCLAIMER in r for r in reasons)


def test_hard_facts_not_in_sheet_are_flagged(profile):
    report, verdict = critic.review(
        profile, "Call us", "Call 0917 123 4567. Since 2015. P500 only.", None
    )
    kinds = {h["kind"] for h in report["hard_facts"]}
    assert {"phone", "year", "price"} <= kinds
    assert verdict == "flagged"


def test_known_phone_is_fine_and_unchecked_without_llm(profile):
    report, verdict = critic.review(profile, "Call us", "Call +63 945 356 6294.", None)
    assert report["hard_facts"] == []
    assert verdict == "unchecked"


def test_step_is_not_a_price(profile):
    report, _ = critic.review(profile, "How", "STEP 1 send a message.", None)
    assert not [h for h in report["hard_facts"] if h["kind"] == "price"]


def test_content_generate_queues_and_checks(profile, store):
    ids = content.generate(profile, FakeLLM(), store, count=2)
    assert len(ids) == 2
    for i in ids:
        d = store.get_draft(i)
        assert d["status"] == "pending"
        assert d["critic_verdict"] in ("clean", "warn", "flagged")


def test_regulated_drafts_get_disclaimer(regulated, tmp_path):
    from beacon.store import Store

    st = Store(tmp_path / "atty")
    ids = content.generate(regulated, FakeLLM(), st, count=1)
    assert st.get_draft(ids[0])["body"].endswith(REGULATED_DISCLAIMER)


def test_flagged_needs_note():
    d = {"status": "pending", "critic_verdict": "flagged"}
    with pytest.raises(PolicyError):
        check_approval(d, "")
    check_approval(d, "Checked with owner; phone is new.")
