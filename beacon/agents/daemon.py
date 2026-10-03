"""Daemon agent: periodically checks Beacon health and suggests improvements.

Usage:
  beacon daemon run --interval 1h     # run indefinitely
  beacon daemon check slicklab-digital  # one-time check
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import click

from ..agents import audit as audit_agent
from ..config import get_settings
from ..profile import find_profile, list_profiles
from ..store import Store

# Settings for what to check
DEFAULT_INTERVAL = 3600  # 1 hour
SSL_WARNING_DAYS = 30
# VISIBILITY_DROP_THRESHOLD = 0.05  # 5% drop triggers alert
# Visibility runs cost ~36 API calls each. Enable with ANTHROPIC_API_KEY in .env
# Run manually: beacon visibility run <client> --label <label>
STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "daemon-state.json"


def send_telegram(message: str) -> bool:
    """Send an alert via the Hermes gateway's Telegram platform."""
    try:
        subprocess.run(
            ["hermes", "send", "-t", "telegram", "-q", message],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return True
    except Exception as e:
        print(f"  Telegram alert failed: {e}")
        return False


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def current_issues(profiles: list[str]) -> tuple[dict[str, str], dict[str, dict]]:
    """Snapshot of every open issue + per-profile results, in one audit pass."""
    issues: dict[str, str] = {}
    results: dict[str, dict] = {}
    for pid in profiles:
        result = check_profile(pid)
        results[pid] = result
        for label in result["errors"]:
            issues[f"{pid}:fail:{label}"] = f"[{pid}] FAIL: {label}"
        for label in result["warnings"]:
            issues[f"{pid}:warn:{label}"] = f"[{pid}] warn: {label}"
    ssl_warn = check_ssl_expiry()
    if ssl_warn:
        issues["ssl:expiry"] = f"SSL: {ssl_warn}"
    return issues, results


def reconcile_alerts(profiles: list[str]) -> tuple[dict[str, str], dict[str, dict]]:
    """Compare current issues to last run. Alert on new + resolved.

    Returns (open issues, per-profile audit results) so callers can print
    without re-running the audit.
    """
    issues, results = current_issues(profiles)
    prev = load_state()

    new = {k: v for k, v in issues.items() if k not in prev}
    resolved = {k: v for k, v in prev.items() if k not in issues}

    for key, line in new.items():
        send_telegram(f"🚨 Beacon alert (new)\n{line}")
    for key, line in resolved.items():
        send_telegram(f"✅ Beacon resolved\n{line}")

    save_state(issues)
    return issues, results


def check_ssl_expiry() -> str | None:
    """Check if SSL cert expires soon. Returns warning message or None."""
    from datetime import datetime, timezone

    cert_path = Path("/etc/letsencrypt/live/beacon.slicklab.digital/fullchain.pem")
    if not cert_path.exists():
        return f"No cert file at {cert_path}"

    try:
        import subprocess

        out = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        # out looks like: notAfter=Oct  2 16:13:51 2027 GMT
        date_str = out.split("=", 1)[1]
        exp_date = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
        exp_date = exp_date.replace(tzinfo=timezone.utc)
        days_left = (exp_date - datetime.now(timezone.utc)).days
        if days_left < SSL_WARNING_DAYS:
            return f"SSL expires in {days_left} days (on {exp_date.date()})"
    except Exception as e:
        return f"Could not check SSL: {e}"
    return None


def check_profile(profile_id: str) -> dict:
    """Run audit for a single profile, return results."""
    settings = get_settings()
    profile = find_profile(settings.profiles_dir, profile_id)
    store = Store(settings.client_dir(profile_id))

    checks = audit_agent.run_audit(profile, settings.places_api_key)
    results = audit_agent.results_as_dicts(checks)

    fails = [r for r in results if r["status"] == "fail"]
    warns = [r for r in results if r["status"] == "warn"]

    return {
        "profile": profile_id,
        "pass": len(results) - len(fails) - len(warns),
        "warn": len(warns),
        "fail": len(fails),
        "warnings": [r["label"] for r in warns],
        "errors": [r["label"] for r in fails],
    }


def daemon_loop(profile_ids: list[str], interval: int = DEFAULT_INTERVAL):
    """Run checks periodically."""
    print(f"Beacon daemon started. Checking {len(profile_ids)} profile(s) every {interval}s.")
    print("Press Ctrl+C to stop.\n")

    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{now}] Running checks...")

            # One audit pass: alert on new + resolved issues, keep results for printing
            issues, results = reconcile_alerts(profile_ids)
            for pid in profile_ids:
                result = results[pid]
                if result["warnings"]:
                    print(f"  [{pid}] {result['warn']} warning(s): {', '.join(result['warnings'])}")
                if result["errors"]:
                    print(f"  [{pid}] {result['fail']} error(s): {', '.join(result['errors'])}")
                if not result["warnings"] and not result["errors"]:
                    print(f"  [{pid}] ✓ All checks pass")

            # Check SSL
            ssl_warn = check_ssl_expiry()
            if ssl_warn:
                print(f"  SSL: ⚠️ {ssl_warn}")
            if issues:
                print(f"  Open issues: {len(issues)}")

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\nDaemon stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)  # Wait before retrying


@click.group()
def cli() -> None:
    """Beacon daemon agent for constant monitoring and improvement."""


@cli.command("check")
@click.argument("client_id")
def check_cmd(client_id: str) -> None:
    """One-time health check for a profile."""
    result = check_profile(client_id)
    print(f"\nProfile: {result['profile']}")
    print(f"  Audit: {result['pass']} pass, {result['warn']} warn, {result['fail']} fail")
    if result["warnings"]:
        for w in result["warnings"]:
            print(f"    ⚠️ {w}")
    if result["errors"]:
        for e in result["errors"]:
            print(f"    ✘ {e}")

    ssl_warn = check_ssl_expiry()
    if ssl_warn:
        print(f"  SSL: ⚠️ {ssl_warn}")


@cli.command("run")
@click.option("--interval", default=3600, help="Seconds between checks (default 1h)")
def run_cmd(interval: int) -> None:
    """Run daemon indefinitely, checking all profiles at interval."""
    s = get_settings()
    profile_ids = [p.id for p in list_profiles(s.profiles_dir)]
    if not profile_ids:
        print("No profiles found. Create a YAML file in profiles/ directory.")
        sys.exit(1)
    daemon_loop(profile_ids, interval)


if __name__ == "__main__":
    cli()