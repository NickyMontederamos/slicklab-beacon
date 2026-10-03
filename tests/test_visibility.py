from beacon.agents import visibility
from beacon.llm import FakeLLM, LLMError


def test_detect_names_domain_and_phone(profile):
    assert visibility.detect(profile, "Try ACME studio downtown.") == ["Acme Studio", "Acme"]
    assert "acme.test" in visibility.detect(profile, "See acme.test for details")
    assert "phone" in visibility.detect(profile, "Call 0945-356-6294")
    assert visibility.detect(profile, "Try Acmeworks instead") == []


def test_run_and_compare(profile, store):
    before = visibility.run(profile, FakeLLM(), store, "baseline")
    after = visibility.run(profile, FakeLLM(["Acme Studio"], mention_every=1), store, "after")
    s = visibility.summarize(store, before)
    assert s["total"] == 4 and s["mentions"] == 0
    c = visibility.compare(store, before, after)
    assert c["after"]["rate"] == 1.0
    assert c["delta"] == 1.0
    assert c["p_value"] is not None and c["p_value"] < 0.05


def test_errors_are_excluded(profile, store):
    class Broken(FakeLLM):
        def ask(self, question, web_search=False):
            raise LLMError("boom")

    rid = visibility.run(profile, Broken(), store, "broken")
    s = visibility.summarize(store, rid)
    assert s["total"] == 0 and s["errors"] == 4


def test_small_samples_are_not_called_real(profile, store):
    p = visibility.two_proportion_p(0, 3, 1, 3)
    assert p > 0.05
