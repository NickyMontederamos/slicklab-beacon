import re

import pytest
from starlette.testclient import TestClient

from beacon.auth import Users
from beacon.config import Settings
from beacon.store import Store
from beacon.web.app import create_app
from tests.conftest import PROFILE


@pytest.fixture
def env(tmp_path):
    import yaml

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "acme.yaml").write_text(yaml.safe_dump(PROFILE))
    other = {**PROFILE, "id": "other", "name": "Other Co"}
    (profiles / "other.yaml").write_text(yaml.safe_dump(other))
    s = Settings(
        data_dir=tmp_path / "data",
        profiles_dir=profiles,
        model="fake-1",
        llm="fake",
        secret_key="x" * 40,
        https=False,
        places_api_key=None,
    )
    app = create_app(s)
    users = Users(s.auth_db)
    return s, TestClient(app), users


def _login(client, token):
    page = client.get("/login").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = client.post("/login", data={"token": token, "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    # the session (and its CSRF token) is rotated on login
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


def test_requires_login(env):
    _, client, _ = env
    r = client.get("/c/acme", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_client_isolation(env):
    _, client, users = env
    token = users.add("Owner", "client", "acme")
    _login(client, token)
    assert client.get("/c/acme").status_code == 200
    assert client.get("/c/other").status_code == 404
    assert client.get("/c/other/inbox").status_code == 404


def test_approve_flow_and_csrf(env):
    s, client, users = env
    store = Store(s.client_dir("acme"))
    i = store.add_draft("faq", "Where?", "Cebu City.", [], "test")
    store.set_critic(i, {"policy": [], "hard_facts": [], "claims": []}, "flagged")
    csrf = _login(client, users.add("Admin", "admin"))

    r = client.post(f"/c/acme/drafts/{i}/approve", data={"csrf": "wrong"})
    assert r.status_code == 403

    client.post(f"/c/acme/drafts/{i}/approve", data={"csrf": csrf, "note": ""})
    assert store.get_draft(i)["status"] == "pending"  # flagged needs a note

    client.post(f"/c/acme/drafts/{i}/approve", data={"csrf": csrf, "note": "ok'd by owner"})
    assert store.get_draft(i)["status"] == "approved"
    assert store.latest_approval(i)["actor"] == "web:Admin"


def test_edit_resets_to_pending_and_rechecks(env):
    s, client, users = env
    store = Store(s.client_dir("acme"))
    i = store.add_draft("faq", "Where?", "Cebu City.", [], "test")
    store.decide(i, "approve", "x")
    csrf = _login(client, users.add("Admin", "admin"))
    client.post(
        f"/c/acme/drafts/{i}/edit",
        data={"csrf": csrf, "title": "Where?", "body": "Call 0917 000 1111"},
    )
    d = store.get_draft(i)
    assert d["status"] == "pending" and d["critic_verdict"] == "flagged"


def test_pages_render(env):
    s, client, users = env
    from beacon.agents import visibility
    from beacon.llm import FakeLLM
    from beacon.profile import Profile

    store = Store(s.client_dir("acme"))
    store.add_audit([{"id": "x", "label": "X", "status": "pass", "detail": "<b>ok</b>"}])
    rid = visibility.run(Profile.model_validate(PROFILE), FakeLLM(), store, "baseline")
    i = store.add_draft("faq", "<script>alert(1)</script>", "body", [], "t")
    _login(client, users.add("Admin", "admin"))
    for path in [
        "/",
        "/c/acme",
        "/c/acme/inbox?status=all",
        f"/c/acme/drafts/{i}",
        "/c/acme/audit",
        f"/c/acme/visibility?before={rid}&after={rid}",
    ]:
        r = client.get(path)
        assert r.status_code == 200, path
    assert "<script>alert(1)</script>" not in client.get(f"/c/acme/drafts/{i}").text


def test_run_buttons_admin_only(env):
    s, client, users = env
    csrf = _login(client, users.add("Owner", "client", "acme"))
    for kind in ("audit", "draft", "publish"):
        assert client.post(f"/c/acme/run/{kind}", data={"csrf": csrf}).status_code == 403
    assert "Run something" not in client.get("/c/acme").text


def test_generate_drafts_button_runs_in_background(env):
    s, client, users = env
    csrf = _login(client, users.add("Admin", "admin"))
    assert "Run something" in client.get("/c/acme").text
    client.post("/c/acme/run/draft", data={"csrf": csrf, "count": "2", "topic": "hours"})
    client.app.state.jobs.join_all()
    store = Store(s.client_dir("acme"))
    assert len(store.list_drafts("pending")) == 2
    kinds = [e["kind"] for e in store.events()]
    assert "job.draft.done" in kinds
    page = client.get("/c/acme").text
    assert "waiting for your decision" in page


def test_draft_job_failure_is_shown(env, monkeypatch):
    s, client, users = env
    csrf = _login(client, users.add("Admin", "admin"))

    def boom(settings):
        raise RuntimeError("no API key")

    monkeypatch.setattr("beacon.web.app.get_llm", boom)
    client.post("/c/acme/run/draft", data={"csrf": csrf, "count": "2"})
    client.app.state.jobs.join_all()
    page = client.get("/c/acme").text
    assert "job.draft.error" in page and "no API key" in page


def test_audit_and_publish_buttons(env, monkeypatch):
    from beacon.agents.audit import Check

    s, client, users = env
    csrf = _login(client, users.add("Admin", "admin"))
    monkeypatch.setattr(
        "beacon.web.app.audit_agent.run_audit",
        lambda profile, places_api_key=None: [Check("w", "Website", "fail", "down")],
    )
    r = client.post("/c/acme/run/audit", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    store = Store(s.client_dir("acme"))
    assert store.audits()[0]["failed"] == 1

    # publish with nothing approved is refused with a message, not a crash
    r = client.post("/c/acme/run/publish", data={"csrf": csrf}, follow_redirects=True)
    assert "Nothing to publish" in r.text

    i = store.add_draft("faq", "Where?", "Cebu City.", [], "t")
    store.set_critic(i, {}, "clean")
    store.decide(i, "approve", "x")
    client.post("/c/acme/run/publish", data={"csrf": csrf})
    assert store.get_draft(i)["status"] == "published"
