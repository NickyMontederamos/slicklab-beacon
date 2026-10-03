"""Daemon agent: periodically checks Beacon health and suggests improvements.

Usage:
  beacon daemon run --interval 1h     # run indefinitely
  beacon daemon check slicklab-digital  # one-time check
"""

from __future__ import annotations

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

            # Check each profile
            for pid in profile_ids:
                result = check_profile(pid)
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