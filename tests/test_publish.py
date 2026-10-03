import json

import pytest

from beacon.policy import PolicyError
from beacon.publish import publish


def _approved(store, title="Where are you?", body="Cebu City."):
    i = store.add_draft("faq", title, body, [], "test")
    store.set_critic(i, {}, "clean")
    store.decide(i, "approve", "tester")
    return i


def test_nothing_to_publish(profile, store, tmp_path):
    with pytest.raises(PolicyError, match="Nothing"):
        publish(profile, store, tmp_path / "out", "tester")


def test_publish_exports_and_marks(profile, store, tmp_path):
    i = _approved(store)
    pending = store.add_draft("faq", "Not approved", "x", [], "test")
    out = publish(profile, store, tmp_path / "out", "tester")
    schema = json.loads((out / "faq-schema.jsonld").read_text())
    assert schema["mainEntity"][0]["name"] == "Where are you?"
    assert "Not approved" not in (out / "faq.md").read_text()
    assert store.get_draft(i)["status"] == "published"
    assert store.get_draft(pending)["status"] == "pending"


def test_edit_after_approval_requires_reapproval(profile, store, tmp_path):
    i = _approved(store)
    store.update_body(i, "Where are you?", "Changed", "editor")
    assert store.get_draft(i)["status"] == "pending"
    with pytest.raises(PolicyError):
        publish(profile, store, tmp_path / "out", "tester")


def test_tampered_content_blocked(profile, store, tmp_path):
    import sqlite3

    i = _approved(store)
    with sqlite3.connect(store.path) as c:
        c.execute("UPDATE drafts SET body = 'sneaky' WHERE id = ?", (i,))
    with pytest.raises(PolicyError, match="changed after approval"):
        publish(profile, store, tmp_path / "out", "tester")


def test_regulated_never_publishes(regulated, store, tmp_path):
    _approved(store)
    with pytest.raises(PolicyError, match="regulated"):
        publish(regulated, store, tmp_path / "out", "tester")


def test_published_record_is_final(profile, store, tmp_path):
    i = _approved(store)
    publish(profile, store, tmp_path / "out", "tester")
    with pytest.raises(ValueError, match="already published"):
        store.decide(i, "reject", "someone")
