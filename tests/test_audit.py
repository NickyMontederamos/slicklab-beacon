import json

import httpx

from beacon.agents.audit import run_audit

GOOD_SCHEMA = {
    "@context": "https://schema.org",
    "@type": "LocalBusiness",
    "name": "Acme Studio",
    "telephone": "+639453566294",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "Salinas Drive, Ucma Village",
        "addressLocality": "Cebu City",
        "postalCode": "6000",
    },
    "openingHoursSpecification": [
        {
            "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "opens": "07:00",
            "closes": "19:00",
        },
        {
            "dayOfWeek": ["https://schema.org/Saturday", "Sunday"],
            "opens": "07:00",
            "closes": "15:00",
        },
    ],
    "sameAs": ["https://maps.app.goo.gl/abc123"],
}


def _client(schema: dict, llms: str | None, footer: str) -> httpx.Client:
    html = (
        f"<html><head><script type='application/ld+json'>{json.dumps(schema)}</script>"
        f"<script>var x='0000000000';</script></head><body><footer>{footer}</footer></body></html>"
    )

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/llms.txt":
            return httpx.Response(200, text=llms) if llms else httpx.Response(404)
        return httpx.Response(200, text=html)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _by_id(checks):
    return {c.id: c.status for c in checks}


def test_consistent_site_passes(profile):
    footer = "Call 0945 356 6294 · Salinas Dr, Ucma Village, 6000 Cebu"
    llms = "# Acme Studio\nPhone: +63 945 356 6294\nAddress: Salinas Dr, Ucma Village"
    s = _by_id(run_audit(profile, http=_client(GOOD_SCHEMA, llms, footer)))
    assert s["website"] == "pass"
    for k in (
        "schema",
        "schema-name",
        "schema-phone",
        "schema-address",
        "schema-hours",
        "schema-google",
        "page-phone",
        "page-address",
        "llms-txt",
    ):
        assert s[k] == "pass", k
    assert s["google"] == "skip"


def test_mismatches_fail(profile):
    bad = {
        **GOOD_SCHEMA,
        "telephone": "+63 917 000 0000",
        "openingHoursSpecification": [{"dayOfWeek": "Monday", "opens": "08:00", "closes": "17:00"}],
        "sameAs": [],
    }
    s = _by_id(run_audit(profile, http=_client(bad, None, "nothing here")))
    assert s["schema-phone"] == "fail"
    assert s["schema-hours"] == "fail"
    assert s["schema-google"] == "warn"
    assert s["page-phone"] == "warn"
    assert s["llms-txt"] == "fail"


def test_no_website(profile):
    p = profile.model_copy(update={"website": None})
    s = _by_id(run_audit(p, http=_client(GOOD_SCHEMA, None, "")))
    assert s["website"] == "warn"
