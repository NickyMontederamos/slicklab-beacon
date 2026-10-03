"""`beacon` command line. Run `beacon --help` for the list of commands."""

from __future__ import annotations

import getpass
import json
import sys

import click

from .agents import audit as audit_agent
from .agents import content, critic, visibility
from .agents.daemon import check_profile, daemon_loop, check_ssl_expiry
from .auth import Users
from .config import get_settings
from .llm import LLMError, get_llm, get_visibility_llm, list_free_openrouter_models
from .policy import PolicyError, check_approval
from .profile import find_profile, list_profiles
from .publish import publish as do_publish
from .store import Store

STATUS_ICON = {"pass": "✔", "warn": "!", "fail": "✘", "skip": "-"}


def _ctx(client_id: str):
    s = get_settings()
    try:
        profile = find_profile(s.profiles_dir, client_id)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    return s, profile, Store(s.client_dir(client_id))


def _actor(by: str | None) -> str:
    return by or f"cli:{getpass.getuser()}"


@click.group()
@click.version_option(package_name="slicklab-beacon")
def cli() -> None:
    """SlickLab Beacon - online presence and AI visibility, human-approved."""


@cli.command("profiles")
def profiles_cmd() -> None:
    """List and validate every profile in the profiles directory."""
    s = get_settings()
    try:
        items = list_profiles(s.profiles_dir)
    except Exception as e:  # noqa: BLE001 - show the validation error to the operator
        raise click.ClickException(f"Profile error: {e}") from e
    for p in items:
        flags = " [regulated: draft-only]" if p.regulated else ""
        click.echo(
            f"{p.id:<24} {p.name}{flags}  ({len(p.fact_sheet())} facts, "
            f"{len(p.visibility.questions)} questions)"
        )


@cli.command("audit")
@click.argument("client_id")
def audit_cmd(client_id: str) -> None:
    """Check website, schema, llms.txt and Google listing agree with the profile."""
    s, profile, store = _ctx(client_id)
    checks = audit_agent.run_audit(profile, places_api_key=s.places_api_key)
    results = audit_agent.results_as_dicts(checks)
    run_id = store.add_audit(results)
    store.log("audit-agent", "audit.run", {"audit_id": run_id})
    for c in checks:
        click.echo(f" {STATUS_ICON[c.status]} {c.label:<32} {c.detail}")
    fails = sum(c.status == "fail" for c in checks)
    click.echo(f"\nAudit #{run_id}: {fails} failing check(s).")


@cli.command("draft")
@click.argument("client_id")
@click.option("--count", default=5, show_default=True)
@click.option("--kind", type=click.Choice(["faq", "explainer"]), default="faq", show_default=True)
@click.option("--topic", default=None, help="Optional focus, e.g. 'hours and location'.")
def draft_cmd(client_id: str, count: int, kind: str, topic: str | None) -> None:
    """Draft FAQs/explainers from the profile's facts. Each draft is critic-checked and queued."""
    s, profile, store = _ctx(client_id)
    ids = content.generate(profile, get_llm(s), store, count=count, kind=kind, topic=topic)
    for i in ids:
        d = store.get_draft(i)
        click.echo(f" #{i:<4} [{d['critic_verdict']:<9}] {d['title']}")
    click.echo(f"\n{len(ids)} draft(s) waiting for review in the inbox.")


@cli.command("critic")
@click.argument("client_id")
@click.option("--draft", "draft_id", type=int, default=None, help="Re-check one draft.")
def critic_cmd(client_id: str, draft_id: int | None) -> None:
    """Re-run the critic on one draft, or on every pending draft."""
    s, profile, store = _ctx(client_id)
    llm = get_llm(s)
    ids = [draft_id] if draft_id else [d["id"] for d in store.list_drafts("pending")]
    for i in ids:
        click.echo(f" #{i}: {critic.review_draft(profile, store, i, llm)}")


@cli.command("queue")
@click.argument("client_id")
@click.option("--status", type=click.Choice(["pending", "approved", "rejected", "published"]))
def queue_cmd(client_id: str, status: str | None) -> None:
    """List drafts in the approval queue."""
    _, _, store = _ctx(client_id)
    for d in store.list_drafts(status):
        click.echo(f" #{d['id']:<4} {d['status']:<9} {d['critic_verdict']:<9} {d['title']}")


@cli.command("show")
@click.argument("client_id")
@click.argument("draft_id", type=int)
def show_cmd(client_id: str, draft_id: int) -> None:
    """Show a draft with its critic report and decision history."""
    _, _, store = _ctx(client_id)
    d = store.get_draft(draft_id)
    if not d:
        raise click.ClickException(f"No draft #{draft_id}")
    click.echo(f"#{d['id']} [{d['status']}] {d['title']}\n\n{d['body']}\n")
    click.echo(json.dumps(d["critic"], indent=2))
    for dec in store.decisions(draft_id):
        click.echo(f" {dec['at']} {dec['action']} by {dec['actor']}: {dec['note'] or ''}")


@cli.command("approve")
@click.argument("client_id")
@click.argument("draft_id", type=int)
@click.option("--note", default="")
@click.option("--by", default=None, help="Reviewer name for the approval record.")
def approve_cmd(client_id: str, draft_id: int, note: str, by: str | None) -> None:
    """Approve a draft (flagged drafts need --note)."""
    _, _, store = _ctx(client_id)
    d = store.get_draft(draft_id)
    if not d:
        raise click.ClickException(f"No draft #{draft_id}")
    try:
        check_approval(d, note)
    except PolicyError as e:
        raise click.ClickException(str(e)) from e
    store.decide(draft_id, "approve", _actor(by), note)
    click.echo(f"Approved #{draft_id}.")


@cli.command("reject")
@click.argument("client_id")
@click.argument("draft_id", type=int)
@click.option("--note", default="")
@click.option("--by", default=None)
def reject_cmd(client_id: str, draft_id: int, note: str, by: str | None) -> None:
    """Reject a draft."""
    _, _, store = _ctx(client_id)
    if not store.get_draft(draft_id):
        raise click.ClickException(f"No draft #{draft_id}")
    try:
        store.decide(draft_id, "reject", _actor(by), note)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Rejected #{draft_id}.")


@cli.command("publish")
@click.argument("client_id")
@click.option("--by", default=None)
def publish_cmd(client_id: str, by: str | None) -> None:
    """Export approved drafts (FAQ markdown + FAQPage JSON-LD). Refuses regulated profiles."""
    s, profile, store = _ctx(client_id)
    try:
        out = do_publish(profile, store, s.client_dir(client_id) / "publish", _actor(by))
    except PolicyError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Exported to {out}")


@cli.group("visibility")
def visibility_group() -> None:
    """Measure how often AI assistants name the business."""


@visibility_group.command("run")
@click.argument("client_id")
@click.option("--label", required=True, help="e.g. baseline, after-schema-fix")
@click.option("--runs", type=int, default=None, help="Times to ask each question.")
@click.option(
    "--web/--no-web",
    default=True,
    show_default=True,
    help="Let the assistant search the web (closer to real assistants).",
)
@click.option("--yes", is_flag=True, help="Skip the cost confirmation.")
def visibility_run(client_id: str, label: str, runs: int | None, web: bool, yes: bool) -> None:
    s, profile, store = _ctx(client_id)
    try:
        llm = get_visibility_llm(s)
    except (LLMError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    runs = runs or profile.visibility.runs_per_question
    calls = runs * len(profile.visibility.questions)
    if not getattr(llm, "free", False) and not yes:
        click.confirm(f"This makes {calls} API calls to {llm.model}. Continue?", abort=True)
    with click.progressbar(length=calls, label="Asking") as bar:
        run_id = visibility.run(
            profile, llm, store, label, runs, web, progress=lambda _: bar.update(1)
        )
    _print_summary(visibility.summarize(store, run_id))


@cli.command("free-models")
def free_models_cmd() -> None:
    """List free OpenRouter models (names change often). Put your pick in BEACON_FREE_MODEL."""
    try:
        models = list_free_openrouter_models()
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"Couldn't reach OpenRouter: {e}") from e
    for m in models[:40]:
        click.echo(f"{m['id']:<60} context {m['context']}")
    click.echo(
        "\nSet BEACON_VISIBILITY_PROVIDER=openrouter, OPENROUTER_API_KEY and "
        "BEACON_FREE_MODEL=<one of the above> in .env."
    )


def _print_summary(summary: dict) -> None:
    r = summary["run"]
    lo, hi = summary["ci"]
    click.echo(
        f"\nRun #{r['id']} '{r['label']}' ({r['provider']}, {r['model']}): named in "
        f"{summary['mentions']}/{summary['total']} answers = {summary['rate']:.0%} "
        f"(95% CI {lo:.0%}-{hi:.0%}); {summary['errors']} error(s)."
    )
    for q in summary["per_question"]:
        click.echo(f"  {q['mentions']}/{q['total']}  {q['question']}")


@visibility_group.command("report")
@click.argument("client_id")
@click.option("--run", "run_id", type=int, default=None, help="Defaults to the latest run.")
def visibility_report(client_id: str, run_id: int | None) -> None:
    _, _, store = _ctx(client_id)
    runs = store.visibility_runs()
    if not runs:
        raise click.ClickException("No visibility runs yet.")
    _print_summary(visibility.summarize(store, run_id or runs[0]["id"]))


@visibility_group.command("compare")
@click.argument("client_id")
@click.argument("before_id", type=int)
@click.argument("after_id", type=int)
def visibility_compare(client_id: str, before_id: int, after_id: int) -> None:
    _, _, store = _ctx(client_id)
    c = visibility.compare(store, before_id, after_id)
    if c["warning"]:
        click.secho(f"WARNING: {c['warning']}", fg="yellow")
    click.echo(
        f"Before {c['before']['rate']:.0%} -> after {c['after']['rate']:.0%} "
        f"({c['delta']:+.0%}). {c['verdict']}"
    )
    for q in c["questions"]:
        before = "  -  " if q["before"] is None else f"{q['before']:.0%}"
        click.echo(f"  {before:>5} -> {q['after']:.0%}  {q['question']}")


@cli.group("user")
def user_group() -> None:
    """Manage web inbox logins."""


@user_group.command("add")
@click.argument("name")
@click.option("--role", type=click.Choice(["admin", "client"]), required=True)
@click.option("--client", "client_id", default=None, help="Required for --role client.")
def user_add(name: str, role: str, client_id: str | None) -> None:
    s = get_settings()
    if role == "client":
        if not client_id:
            raise click.ClickException("--client is required for client users.")
        find_profile(s.profiles_dir, client_id)
    token = Users(s.auth_db).add(name, role, client_id)
    click.echo(f"Created {role} user {name!r}. Login token (shown once):\n\n  {token}\n")


@user_group.command("list")
def user_list() -> None:
    for u in Users(get_settings().auth_db).list():
        state = "disabled" if u["disabled"] else "active"
        click.echo(
            f" {u['id']:<4} {u['name']:<20} {u['role']:<7} {u['client_id'] or '*':<20} {state}"
        )


@user_group.command("disable")
@click.argument("user_id", type=int)
def user_disable(user_id: int) -> None:
    Users(get_settings().auth_db).disable(user_id)
    click.echo(f"Disabled user {user_id}.")


@cli.group("daemon")
def daemon_group() -> None:
    """Constant monitoring and improvement agent."""


@daemon_group.command("check")
@click.argument("client_id")
def daemon_check(client_id: str) -> None:
    """One-time health check for a profile."""
    result = check_profile(client_id)
    click.echo(f"\nProfile: {result['profile']}")
    click.echo(f"  Audit: {result['pass']} pass, {result['warn']} warn, {result['fail']} fail")
    if result["warnings"]:
        for w in result["warnings"]:
            click.echo(f"    ⚠️ {w}")
    if result["errors"]:
        for e in result["errors"]:
            click.echo(f"    ✘ {e}")
    ssl_warn = check_ssl_expiry()
    if ssl_warn:
        click.echo(f"  SSL: ⚠️ {ssl_warn}")


@daemon_group.command("run")
@click.option("--interval", default=3600, help="Seconds between checks (default 1h)")
def daemon_run_cmd(interval: int) -> None:
    """Run daemon indefinitely, checking all profiles at interval."""
    s = get_settings()
    profile_ids = [p.id for p in list_profiles(s.profiles_dir)]
    if not profile_ids:
        click.echo("No profiles found. Create a YAML file in profiles/ directory.")
        sys.exit(1)
    daemon_loop(profile_ids, interval)


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8077, show_default=True)
def serve_cmd(host: str, port: int) -> None:
    """Run the web inbox."""
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
