"""Audit agent: do the website, its schema, llms.txt and the Google listing agree with the profile?

No LLM involved - every check is deterministic so results are repeatable and explainable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from ..normalize import address_match, fold, names_match, phone_digits, phones_in
from ..profile import Profile

USER_AGENT = "SlickLabBeacon/0.1 (+https://beacon.slicklab.digital)"
DAY_URI = {
    "Mo": "Monday",
    "Tu": "Tuesday",
    "We": "Wednesday",
    "Th": "Thursday",
    "Fr": "Friday",
    "Sa": "Saturday",
    "Su": "Sunday",
}
BUSINESS_TYPES_HINT = (
    "business",
    "organization",
    "service",
    "attorney",
    "legal",
    "store",
    "office",
)


@dataclass
class Check:
    id: str
    label: str
    status: str  # pass | warn | fail | skip
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.text: list[str] = []
        self.links: list[str] = []
        self._in_jsonld = False
        self._skip = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._buf = []
        elif tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._buf))
            self._in_jsonld = False
        elif tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)
        elif not self._skip:
            self.text.append(data)


def _flatten_jsonld(raw_blocks: list[str]) -> list[dict]:
    nodes: list[dict] = []

    def walk(obj):
        if isinstance(obj, list):
            for o in obj:
                walk(o)
        elif isinstance(obj, dict):
            if "@graph" in obj:
                walk(obj["@graph"])
            if "@type" in obj:
                nodes.append(obj)

    for raw in raw_blocks:
        try:
            walk(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return nodes


def _types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else list(t)


def _pick_business(nodes: list[dict], profile: Profile) -> dict | None:
    candidates = [
        n for n in nodes if any(h in fold(t) for t in _types(n) for h in BUSINESS_TYPES_HINT)
    ]
    for n in candidates:
        if names_match(str(n.get("name", "")), profile.all_names):
            return n
    return candidates[0] if candidates else None


def _address_text(addr) -> str:
    if isinstance(addr, str):
        return addr
    if isinstance(addr, dict):
        keys = ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
        return " ".join(str(addr.get(k, "")) for k in keys)
    if isinstance(addr, list) and addr:
        return _address_text(addr[0])
    return ""


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _schema_hours(node: dict) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for spec in _as_list(node.get("openingHoursSpecification")):
        if not isinstance(spec, dict):
            continue
        for day in _as_list(spec.get("dayOfWeek")):
            day_name = str(day).rsplit("/", 1)[-1]
            out.add((day_name, str(spec.get("opens", ""))[:5], str(spec.get("closes", ""))[:5]))
    return out


def _profile_hours(profile: Profile) -> set[tuple[str, str, str]]:
    return {(DAY_URI[d], h.opens, h.closes) for h in profile.hours for d in h.days}


def _is_google_maps(url: str) -> bool:
    u = url.lower()
    return any(
        s in u
        for s in ("google.com/maps", "maps.google.", "goo.gl/maps", "g.page", "maps.app.goo.gl")
    )


def _fetch(client: httpx.Client, url: str) -> tuple[httpx.Response | None, str]:
    try:
        return client.get(url, follow_redirects=True), ""
    except httpx.HTTPError as e:
        return None, f"{type(e).__name__}: {e}"


def run_audit(
    profile: Profile,
    http: httpx.Client | None = None,
    places_api_key: str | None = None,
) -> list[Check]:
    own = http is None
    client = http or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})
    try:
        checks = (
            _site_checks(profile, client)
            if profile.website
            else [
                Check("website", "Website", "warn", "No website in profile - site checks skipped.")
            ]
        )
        checks.append(_google_check(profile, client, places_api_key))
        return checks
    finally:
        if own:
            client.close()


def _site_checks(profile: Profile, client: httpx.Client) -> list[Check]:
    checks: list[Check] = []
    resp, err = _fetch(client, profile.website)
    if resp is None or resp.status_code >= 400:
        code = f"no response ({err})" if resp is None else f"HTTP {resp.status_code}"
        return [Check("website", "Website reachable", "fail", f"{profile.website}: {code}")]
    checks.append(Check("website", "Website reachable", "pass", f"{resp.url} returned 200"))

    parser = _PageParser()
    parser.feed(resp.text)
    page_text = " ".join(parser.text)
    nodes = _flatten_jsonld(parser.jsonld)
    biz = _pick_business(nodes, profile)
    c = profile.contact

    if biz is None:
        checks.append(
            Check(
                "schema",
                "Business schema (JSON-LD)",
                "fail",
                "No LocalBusiness/Organization JSON-LD found on the home page.",
            )
        )
    else:
        checks.append(
            Check("schema", "Business schema (JSON-LD)", "pass", f"Found {', '.join(_types(biz))}.")
        )
        name = str(biz.get("name", ""))
        checks.append(
            Check(
                "schema-name",
                "Schema name matches",
                "pass" if names_match(name, profile.all_names) else "fail",
                f"Schema says {name!r}; profile says {profile.name!r}.",
            )
        )
        if c.phone:
            tel = str(biz.get("telephone", ""))
            ok = phone_digits(tel) == phone_digits(c.phone)
            checks.append(
                Check(
                    "schema-phone",
                    "Schema phone matches",
                    "pass" if ok else "fail",
                    f"Schema says {tel or '(none)'}; profile says {c.phone}.",
                )
            )
        if c.address:
            found = _address_text(biz.get("address"))
            ok = address_match(c.address.street, c.address.postal_code, found)
            checks.append(
                Check(
                    "schema-address",
                    "Schema address matches",
                    "pass" if ok else "fail",
                    f"Schema says {found.strip() or '(none)'!r}; "
                    f"profile says {c.address.one_line()!r}.",
                )
            )
        if profile.hours:
            want, have = _profile_hours(profile), _schema_hours(biz)
            if not have:
                checks.append(
                    Check(
                        "schema-hours",
                        "Schema opening hours",
                        "warn",
                        "No openingHoursSpecification in schema.",
                    )
                )
            else:
                missing, extra = want - have, have - want
                ok = not missing and not extra
                detail = (
                    "Hours match."
                    if ok
                    else (
                        f"Missing from schema: {sorted(missing)}. Not in profile: {sorted(extra)}."
                    )
                )
                checks.append(
                    Check("schema-hours", "Schema opening hours", "pass" if ok else "fail", detail)
                )
        links = [str(x) for x in _as_list(biz.get("sameAs")) + _as_list(biz.get("hasMap"))]
        gmaps = [u for u in links if _is_google_maps(u)]
        checks.append(
            Check(
                "schema-google",
                "Schema links Google listing",
                "pass" if gmaps else "warn",
                f"Found {gmaps[0]}" if gmaps else "No Google Maps link in sameAs/hasMap.",
            )
        )

    if c.phone:
        ok = phone_digits(c.phone) in phones_in(page_text)
        checks.append(
            Check(
                "page-phone",
                "Phone visible on page",
                "pass" if ok else "warn",
                "Phone number appears in page text."
                if ok
                else f"{c.phone} not found in visible text.",
            )
        )
    if c.address:
        ok = address_match(c.address.street, None, page_text)
        checks.append(
            Check(
                "page-address",
                "Address visible on page",
                "pass" if ok else "warn",
                "Street address appears in page text."
                if ok
                else f"{c.address.street!r} not found in visible text.",
            )
        )

    checks.append(_llms_txt_check(profile, client))
    return checks


def _llms_txt_check(profile: Profile, client: httpx.Client) -> Check:
    url = urljoin(profile.website.rstrip("/") + "/", "llms.txt")
    resp, _ = _fetch(client, url)
    if resp is None or resp.status_code != 200 or not resp.text.strip():
        return Check("llms-txt", "llms.txt", "fail", f"{url} missing or empty.")
    text = resp.text
    problems = []
    if not any(fold(n) in fold(text) for n in profile.all_names):
        problems.append("business name")
    if profile.contact.phone and phone_digits(profile.contact.phone) not in phones_in(text):
        problems.append("phone")
    a = profile.contact.address
    if a and not address_match(a.street, None, text):
        problems.append("address")
    if problems:
        return Check("llms-txt", "llms.txt", "warn", f"Found, but missing: {', '.join(problems)}.")
    return Check(
        "llms-txt", "llms.txt", "pass", "Found, and it names the business, phone and address."
    )


def _google_check(profile: Profile, client: httpx.Client, api_key: str | None) -> Check:
    if not api_key:
        return Check(
            "google",
            "Google Business Profile",
            "skip",
            "Set GOOGLE_PLACES_API_KEY to compare the live Google listing.",
        )
    query = profile.name
    if profile.contact.address:
        query += f" {profile.contact.address.locality}"
    try:
        resp = client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,"
                "places.internationalPhoneNumber,places.googleMapsUri",
            },
            json={"textQuery": query},
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return Check("google", "Google Business Profile", "warn", f"Places API error: {e}")
    places = resp.json().get("places", [])
    if profile.contact.google_place_id:
        places = [p for p in places if p.get("id") == profile.contact.google_place_id] or places
    match = next(
        (
            p
            for p in places
            if names_match(p.get("displayName", {}).get("text", ""), profile.all_names)
        ),
        None,
    )
    if match is None:
        return Check(
            "google",
            "Google Business Profile",
            "fail",
            f"No Google listing named {profile.name!r} found for {query!r}.",
        )
    problems = []
    if profile.contact.phone and phone_digits(
        match.get("internationalPhoneNumber")
    ) != phone_digits(profile.contact.phone):
        problems.append(f"phone is {match.get('internationalPhoneNumber') or '(none)'}")
    a = profile.contact.address
    if a and not address_match(a.street, a.postal_code, match.get("formattedAddress", "")):
        problems.append(f"address is {match.get('formattedAddress')!r}")
    if problems:
        return Check(
            "google",
            "Google Business Profile",
            "fail",
            "Listing found, but " + "; ".join(problems) + ".",
        )
    return Check(
        "google",
        "Google Business Profile",
        "pass",
        f"Listing matches: {match.get('googleMapsUri', '')}",
    )


def results_as_dicts(checks: list[Check]) -> list[dict]:
    return [c.as_dict() for c in checks]
