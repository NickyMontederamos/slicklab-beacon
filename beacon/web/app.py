"""Web inbox: review drafts (Approve / Edit / Reject), see audits and visibility runs.

Server-rendered, no JavaScript build step. Every route checks the logged-in user may see the
client, and every POST carries a CSRF token.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..agents import critic, visibility
from ..auth import Users, can_access
from ..config import Settings, get_settings
from ..llm import LLMError, get_llm
from ..policy import PolicyError, check_approval
from ..profile import Profile, find_profile, list_profiles
from ..store import Store

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _user(request: Request) -> dict | None:
    uid = request.session.get("uid")
    return request.app.state.users.get(uid) if uid else None


def _require_user(request: Request) -> dict:
    user = _user(request)
    if user is None:
        raise HTTPException(303, headers={"Location": "/login"})
    return user


def _client(request: Request) -> tuple[dict, Profile, Store]:
    user = _require_user(request)
    client_id = request.path_params["client_id"]
    if not can_access(user, client_id):
        raise HTTPException(404)
    s = _settings(request)
    try:
        profile = find_profile(s.profiles_dir, client_id)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404) from e
    return user, profile, Store(s.client_dir(client_id))


def _csrf(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


async def _form(request: Request) -> dict:
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "")):
        raise HTTPException(403, "Bad CSRF token - reload the page and try again.")
    return {k: str(v) for k, v in form.items()}


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.update(user=_user(request), csrf=_csrf(request), flash=request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _actor(user: dict) -> str:
    return f"web:{user['name']}"


# -- auth ---------------------------------------------------------------------------


async def login(request: Request):
    if request.method == "POST":
        form = await _form(request)
        user = request.app.state.users.authenticate(form.get("token", ""))
        if user is None:
            flash(request, "That token didn't work.", "error")
            return RedirectResponse("/login", 303)
        request.session.clear()
        request.session["uid"] = user["id"]
        return RedirectResponse("/", 303)
    return render(request, "login.html")


async def logout(request: Request):
    await _form(request)
    request.session.clear()
    return RedirectResponse("/login", 303)


# -- pages --------------------------------------------------------------------------


async def home(request: Request):
    user = _require_user(request)
    s = _settings(request)
    clients = []
    for p in list_profiles(s.profiles_dir):
        if can_access(user, p.id):
            store = Store(s.client_dir(p.id))
            clients.append({"profile": p, "counts": store.counts()})
    if user["role"] == "client" and len(clients) == 1:
        return RedirectResponse(f"/c/{clients[0]['profile'].id}", 303)
    return render(request, "home.html", clients=clients)


async def dashboard(request: Request):
    _, profile, store = _client(request)
    audits = store.audits(limit=1)
    runs = store.visibility_runs()
    latest = visibility.summarize(store, runs[0]["id"]) if runs else None
    return render(
        request,
        "dashboard.html",
        profile=profile,
        counts=store.counts(),
        audit=audits[0] if audits else None,
        runs=runs[:6],
        latest=latest,
        events=store.events(15),
    )


async def inbox(request: Request):
    _, profile, store = _client(request)
    status = request.query_params.get("status", "pending")
    if status not in ("pending", "approved", "rejected", "published", "all"):
        status = "pending"
    drafts = store.list_drafts(None if status == "all" else status)
    return render(
        request, "inbox.html", profile=profile, drafts=drafts, status=status, counts=store.counts()
    )


async def draft_view(request: Request):
    _, profile, store = _client(request)
    d = store.get_draft(int(request.path_params["draft_id"]))
    if d is None:
        raise HTTPException(404)
    facts = {f.id: f.text for f in profile.fact_sheet()}
    return render(
        request, "draft.html", profile=profile, d=d, facts=facts, decisions=store.decisions(d["id"])
    )


async def draft_action(request: Request):
    user, profile, store = _client(request)
    draft_id = int(request.path_params["draft_id"])
    action = request.path_params["action"]
    form = await _form(request)
    d = store.get_draft(draft_id)
    if d is None:
        raise HTTPException(404)
    back = f"/c/{profile.id}/drafts/{draft_id}"
    note = form.get("note", "").strip()

    if action == "approve":
        try:
            check_approval(d, note)
        except PolicyError as e:
            flash(request, str(e), "error")
            return RedirectResponse(back, 303)
        store.decide(draft_id, "approve", _actor(user), note)
        flash(request, f"Approved #{draft_id}.")
    elif action == "reject":
        if d["status"] == "published":
            flash(request, "Published drafts can't be rejected.", "error")
            return RedirectResponse(back, 303)
        store.decide(draft_id, "reject", _actor(user), note)
        flash(request, f"Rejected #{draft_id}.")
    elif action == "edit":
        if d["status"] == "published":
            flash(request, "Published drafts can't be edited.", "error")
            return RedirectResponse(back, 303)
        title, body = form.get("title", "").strip(), form.get("body", "").strip()
        if not title or not body:
            flash(request, "Title and body can't be empty.", "error")
            return RedirectResponse(back, 303)
        store.update_body(draft_id, title, body, _actor(user), note)
        try:
            llm = get_llm(_settings(request))
        except Exception:  # noqa: BLE001 - fall back to rule-only checks
            llm = None
        try:
            verdict = critic.review_draft(profile, store, draft_id, llm)
        except LLMError:
            verdict = critic.review_draft(profile, store, draft_id, None)
        flash(request, f"Saved. Critic re-checked it: {verdict}. It needs approval again.")
    else:
        raise HTTPException(404)
    return RedirectResponse(back if action == "edit" else f"/c/{profile.id}/inbox", 303)


async def audit_view(request: Request):
    _, profile, store = _client(request)
    return render(request, "audit.html", profile=profile, audits=store.audits(limit=10))


async def visibility_view(request: Request):
    _, profile, store = _client(request)
    runs = store.visibility_runs()
    run_id = request.query_params.get("run")
    summary = None
    answers = []
    if runs:
        rid = int(run_id) if run_id and run_id.isdigit() else runs[0]["id"]
        if store.visibility_run(rid):
            summary = visibility.summarize(store, rid)
            answers = store.answers(rid)
    comparison = None
    b, a = request.query_params.get("before"), request.query_params.get("after")
    if (
        b
        and a
        and b.isdigit()
        and a.isdigit()
        and store.visibility_run(int(b))
        and store.visibility_run(int(a))
    ):
        comparison = visibility.compare(store, int(b), int(a))
    return render(
        request,
        "visibility.html",
        profile=profile,
        runs=runs,
        summary=summary,
        answers=answers,
        comparison=comparison,
    )


async def healthz(request: Request):
    return PlainTextResponse("ok")


async def http_error(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers:
        return RedirectResponse(exc.headers["Location"], 303)
    return render(
        request,
        "error.html",
        status_code=exc.status_code,
        status=exc.status_code,
        detail=exc.detail if exc.status_code != 404 else "Not found.",
    )


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or get_settings()
    if not settings.secret_key or len(settings.secret_key) < 32:
        raise RuntimeError("Set BEACON_SECRET_KEY to a random string of 32+ characters.")
    routes = [
        Route("/", home),
        Route("/login", login, methods=["GET", "POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/healthz", healthz),
        Route("/c/{client_id}", dashboard),
        Route("/c/{client_id}/inbox", inbox),
        Route("/c/{client_id}/drafts/{draft_id:int}", draft_view),
        Route("/c/{client_id}/drafts/{draft_id:int}/{action}", draft_action, methods=["POST"]),
        Route("/c/{client_id}/audit", audit_view),
        Route("/c/{client_id}/visibility", visibility_view),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    middleware = [
        Middleware(
            SessionMiddleware,
            secret_key=settings.secret_key,
            https_only=settings.https,
            same_site="lax",
            max_age=60 * 60 * 12,
            session_cookie="beacon_session",
        ),
    ]
    app = Starlette(
        routes=routes, middleware=middleware, exception_handlers={HTTPException: http_error}
    )
    app.state.settings = settings
    app.state.users = Users(settings.auth_db)
    return app
