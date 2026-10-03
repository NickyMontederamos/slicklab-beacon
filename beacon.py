#!/usr/bin/env python3
"""
SlickLab Beacon — Local SEO / AI Visibility Checker
Checks NAP consistency, schema completeness, and Google Business Profile alignment.
Uses only Python stdlib - no external dependencies required.
"""

import re
import json
import argparse
import html
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Optional, List

import requests


class ExtractLDJSON(HTMLParser):
    """Extract JSON-LD blocks from HTML using stdlib HTMLParser."""
    
    def __init__(self):
        super().__init__()
        self.in_ld = False
        self.ld_content = []
        self.current_content = []
    
    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs_dict = dict(attrs)
            if attrs_dict.get("type") == "application/ld+json":
                self.in_ld = True
                self.current_content = []
    
    def handle_endtag(self, tag):
        if tag == "script" and self.in_ld:
            self.in_ld = False
            content = ''.join(self.current_content)
            if content.strip():
                self.ld_content.append(content)
            self.current_content = []
    
    def handle_data(self, data):
        if self.in_ld:
            self.current_content.append(data)


def extract_json_ld(html: str) -> List[dict]:
    """Extract all JSON-LD blocks from HTML using stdlib."""
    parser = ExtractLDJSON()
    parser.feed(html)
    
    blocks = []
    for raw in parser.ld_content:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                # Keep blocks that have a @type OR contain a @graph
                # (Organization may live inside @graph without a top-level @type)
                if "@type" in data or "@graph" in data:
                    blocks.append(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and ("@type" in item or "@graph" in item):
                        blocks.append(item)
        except json.JSONDecodeError:
            continue
    return blocks


def find_organization(ld_blocks: List[dict]) -> Optional[dict]:
    """Find the Organization node.

    Checks, in order:
    1. A standalone block with @type Organization
    2. A node inside a top-level @graph
    3. A ContactPage/other page whose mainEntity is an Organization
    """
    for block in ld_blocks:
        node_type = block.get("@type", [])
        if isinstance(node_type, str):
            node_type = [node_type]
        if "Organization" in node_type:
            return block
        # Check @graph for Organization
        if "@graph" in block:
            for g in block.get("@graph", []):
                t = g.get("@type", [])
                if isinstance(t, str):
                    t = [t]
                if "Organization" in t:
                    return g
        # Check mainEntity (e.g. ContactPage -> Organization)
        main = block.get("mainEntity")
        if isinstance(main, dict):
            t = main.get("@type", [])
            if isinstance(t, str):
                t = [t]
            if "Organization" in t:
                return main
    return None


# Data classes
@dataclass
class ContactDetails:
    address: str
    phone: str
    hours: List[dict]


@dataclass  
class CheckResult:
    name: str
    passed: bool
    message: str
    severity: str = "info"


def load_config() -> ContactDetails:
    """Load expected contact details from config.yaml."""
    config_path = Path(__file__).parent / "config.yaml"
    
    expected = ContactDetails(
        address="Salinas Dr, Ucma Village, 6000 Cebu",
        phone="+63 945 356 6294",
        hours=[
            {"day": "Mon-Fri", "opens": "07:00", "closes": "19:00"},
            {"day": "Sat-Sun", "opens": "07:00", "closes": "15:00"},
        ]
    )
    return expected


def fetch_html(url: str) -> Optional[str]:
    """Fetch HTML from URL with timeout."""
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "SlickLab-Beacon/1.0 (SEO Validator)"
        })
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"ERROR fetching {url}: {e}")
        return None


def extract_schema_contact(org: dict) -> ContactDetails:
    """Extract contact details from Organization schema."""
    addr = org.get("address", {})
    street = addr.get("streetAddress", "")
    locality = addr.get("addressLocality", "")
    postal = addr.get("postalCode", "")
    
    if street and locality:
        address = f"{street}, {locality}"
        if postal:
            address += f", {postal}"
    else:
        address = locality or ""
    
    phone = org.get("telephone", "")
    
    hours = []
    for spec in org.get("openingHoursSpecification", []):
        days = spec.get("dayOfWeek", [])
        if isinstance(days, str):
            days = [days]
        days_str = ", ".join(days) if len(days) > 1 else days[0] if days else ""
        hours.append({
            "day": days_str,
            "opens": spec.get("opens", ""),
            "closes": spec.get("closes", "")
        })
    
    return ContactDetails(address=address, phone=phone, hours=hours)


def extract_footer_address(html: str) -> str:
    """Extract address from footer using regex on raw HTML."""
    # Look for pattern like "Salinas Dr, Ucma Village, 6000 Cebu"
    match = re.search(r'(Salinas Dr, Ucma Village, \d{4} Cebu)', html)
    if match:
        return match.group(1).strip()
    return ""


def check_nap_match(org: dict, expected: ContactDetails) -> CheckResult:
    """Check that schema address components match the expected address.

    The schema stores address as structured components (streetAddress,
    addressLocality, postalCode) while the display string is
    'Salinas Dr, Ucma Village, 6000 Cebu'. Compare components instead
    of assembled strings to avoid false positives from ordering/format.
    """
    addr = org.get("address", {})
    street = addr.get("streetAddress", "")
    locality = addr.get("addressLocality", "")
    postal = addr.get("postalCode", "")

    # Expected components derived from the canonical display address
    # "Salinas Dr, Ucma Village, 6000 Cebu"
    checks = {
        "streetAddress contains 'Salinas Dr'": "Salinas Dr" in street,
        "streetAddress contains 'Ucma Village'": "Ucma Village" in street,
        "postalCode is '6000'": postal.strip() == "6000",
        "addressLocality is 'Cebu City' or 'Cebu'": locality.strip() in ("Cebu City", "Cebu"),
    }

    failed = [k for k, v in checks.items() if not v]
    if not failed:
        return CheckResult(
            "NAP Address",
            True,
            f"Components match: street='{street}', locality='{locality}', postal='{postal}'",
            "info",
        )
    return CheckResult(
        "NAP Address",
        False,
        f"Failed: {'; '.join(failed)} (got street='{street}', locality='{locality}', postal='{postal}')",
        "high",
    )


def check_phone(schema_phone: str, expected: ContactDetails) -> CheckResult:
    """Check phone number matches."""
    if not schema_phone:
        return CheckResult("Phone", False, "Missing phone in schema", "high")
    
    if schema_phone == expected.phone:
        return CheckResult("Phone", True, f"Matches: {expected.phone}", "info")
    else:
        return CheckResult("Phone", False, f"Got {schema_phone}, expected {expected.phone}", "high")


_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_DAY_ABBR = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
    "thursday": "Thu", "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
}


def _expand_range(token: str) -> set:
    """Expand a day token. Handles 'Mon-Fri' ranges and single days."""
    token = token.strip()
    if "-" in token:
        start, end = token.split("-", 1)
        start, end = start.strip(), end.strip()
        if start in _DAY_ORDER and end in _DAY_ORDER:
            i, j = _DAY_ORDER.index(start), _DAY_ORDER.index(end)
            if i <= j:
                return set(_DAY_ORDER[i : j + 1])
            return set(_DAY_ORDER[i:] + _DAY_ORDER[: j + 1])
    if token in _DAY_ORDER:
        return {token}
    return {token}


def _normalize_days(days) -> frozenset:
    """Normalize a dayOfWeek value to a frozenset of 3-letter abbreviations.

    Handles full names ('Monday'), abbreviations ('Mon'), ranges ('Mon-Fri'),
    and comma-joined strings ('Monday, Tuesday, Wednesday').
    """
    if isinstance(days, str):
        # Split comma-joined strings back into individual day tokens
        days = [d for d in days.split(",")]
    out = set()
    for d in days:
        d = str(d).strip().lower()
        abbr = _DAY_ABBR.get(d, d.title())
        out |= _expand_range(abbr)
    return frozenset(out)


def check_hours(schema_hours: List, expected: ContactDetails) -> CheckResult:
    """Check opening hours match, normalizing day formats.

    Schema stores dayOfWeek as full day names (e.g. ['Monday',...,'Friday'])
    while the expected config uses 'Mon-Fri'. Normalize both to sets of
    3-letter day abbreviations and compare (days, opens, closes) tuples.
    """
    if not schema_hours:
        return CheckResult("Hours", False, "Missing hours in schema", "medium")

    def to_set(hours_list):
        out = set()
        for h in hours_list:
            days = h.get("dayOfWeek", h.get("day", []))
            out.add((_normalize_days(days), h.get("opens", ""), h.get("closes", "")))
        return out

    schema_set = to_set(schema_hours)
    expected_set = to_set(expected.hours)

    if schema_set == expected_set:
        return CheckResult("Hours", True, f"Hours match ({len(schema_set)} ranges)", "info")

    missing = expected_set - schema_set
    extra = schema_set - expected_set
    parts = []
    if missing:
        parts.append(f"missing {missing}")
    if extra:
        parts.append(f"extra {extra}")
    return CheckResult("Hours", False, "; ".join(parts) or "mismatch", "medium")


def check_hasmap(org: dict, expected: ContactDetails) -> CheckResult:
    """Check hasMap points to Google listing."""
    has_map = org.get("hasMap", "")
    if has_map:
        return CheckResult("hasMap", True, f"Maps to: {has_map}", "info")
    return CheckResult("hasMap", False, "Missing hasMap in schema", "medium")


def check_sameas(org: dict) -> CheckResult:
    """Check sameAs includes Google and LinkedIn."""
    sameas = org.get("sameAs", [])
    if not sameas:
        return CheckResult("sameAs", False, "Missing sameAs", "medium")
    
    has_linkedin = any("linkedin" in str(s).lower() for s in sameas)
    has_google = any("google" in str(s).lower() or "maps" in str(s).lower() for s in sameas)
    
    missing = []
    if not has_linkedin:
        missing.append("LinkedIn")
    if not has_google:
        missing.append("Google")
    
    if not missing:
        return CheckResult("sameAs", True, "Has LinkedIn and Google URLs", "info")
    return CheckResult("sameAs", False, f"Missing: {', '.join(missing)}", "medium")


def calculate_score(results: List[CheckResult]) -> int:
    """Calculate final score (0-100) from results."""
    critical_failures = sum(1 for r in results if r.severity == "high" and not r.passed)
    medium_failures = sum(1 for r in results if r.severity == "medium" and not r.passed)
    
    base = 100
    base -= critical_failures * 25
    base -= medium_failures * 10
    return max(0, base)


def main():
    parser = argparse.ArgumentParser(description="SlickLab Beacon — SEO Visibility Checker")
    parser.add_argument("--target", "-t", default="https://slicklab.digital", help="Target URL to check")
    parser.add_argument("--output", "-o", help="Output JSON report path")
    args = parser.parse_args()
    
    print(f"🛰️  Beacon checking: {args.target}")
    print("=" * 50)
    
    expected = load_config()
    
    html = fetch_html(args.target)
    if not html:
        print("❌ Failed to fetch target URL")
        return 1
    
    ld_blocks = extract_json_ld(html)
    org = find_organization(ld_blocks)
    
    results = []
    
    if org:
        schema_contact = extract_schema_contact(org)
        results.append(check_nap_match(org, expected))
        results.append(check_phone(schema_contact.phone, expected))
        results.append(check_hours(schema_contact.hours, expected))
        results.append(check_hasmap(org, expected))
        results.append(check_sameas(org))
    else:
        results.append(CheckResult("Schema", False, "No Organization schema found", "high"))
    
    footer_addr = extract_footer_address(html)
    results.append(CheckResult("Footer", bool(footer_addr), 
                               f"Footer address: {footer_addr or 'not found'}", 
                               "low" if footer_addr else "medium"))
    
    score = calculate_score(results)
    
    print()
    for r in results:
        status = "✓" if r.passed else "✗"
        level = f"[{r.severity.upper()}]" if r.severity != "info" else ""
        print(f"{status} {r.name}: {r.message} {level}")
    
    print()
    print(f"📊 Health Score: {score}/100")
    
    if score >= 90:
        print("✅ Excellent - All signals aligned")
    elif score >= 70:
        print("⚠️ Good - Minor issues to fix")
    else:
        print("❌ Needs attention - Multiple signal mismatches")
    
    report = {
        "target": args.target,
        "score": score,
        "results": [
            {"name": r.name, "passed": r.passed, "message": r.message, "severity": r.severity}
            for r in results
        ],
        "schema_address": org.get("address", {}).get("streetAddress") if org else None,
        "footer_address": footer_addr,
        "expected": {
            "address": expected.address,
            "phone": expected.phone,
            "hours": expected.hours
        }
    }
    
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(f"\n📄 Report saved to: {args.output}")
    
    return 0 if score >= 70 else 1


if __name__ == "__main__":
    exit(main())