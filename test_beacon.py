"""Tests for SlickLab Beacon.

Run: python3 test_beacon.py
"""

import json
import unittest

import beacon


# A minimal but realistic Organization JSON-LD block matching the current site.
SAMPLE_LD = json.dumps({
    "@context": "https://schema.org",
    "@graph": [
        {
            "@type": ["Organization", "ProfessionalService", "SoftwareCompany"],
            "@id": "https://example.com/#organization",
            "name": "Example Studio",
            "telephone": "+63 945 356 6294",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "Salinas Dr, Ucma Village",
                "addressLocality": "Cebu City",
                "addressRegion": "PH-07",
                "postalCode": "6000",
                "addressCountry": "PH",
            },
            "openingHoursSpecification": [
                {"dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], "opens": "07:00", "closes": "19:00"},
                {"dayOfWeek": ["Saturday", "Sunday"], "opens": "07:00", "closes": "15:00"},
            ],
            "hasMap": "https://share.google/abc123",
            "sameAs": ["https://www.linkedin.com/in/example", "https://share.google/abc123"],
        },
        {"@type": "WebSite", "@id": "https://example.com/#website"},
    ],
})


def make_html(ld_json=SAMPLE_LD, footer_addr="Salinas Dr, Ucma Village, 6000 Cebu"):
    return f"""<!DOCTYPE html>
<html><head><title>Test</title>
<script type="application/ld+json">
{ld_json}
</script>
</head>
<body><footer><span>{footer_addr}</span></footer></body></html>"""


class TestExtractLDJSON(unittest.TestCase):
    def test_extracts_block(self):
        blocks = beacon.extract_json_ld(make_html())
        self.assertEqual(len(blocks), 1)

    def test_ignores_other_scripts(self):
        html = '<script type="text/javascript">var x=1;</script>'
        self.assertEqual(beacon.extract_json_ld(html), [])


class TestFindOrganization(unittest.TestCase):
    def test_finds_in_graph(self):
        blocks = beacon.extract_json_ld(make_html())
        org = beacon.find_organization(blocks)
        self.assertIsNotNone(org)
        self.assertIn("Organization", org["@type"])

    def test_finds_via_main_entity(self):
        ld = json.dumps({
            "@type": "ContactPage",
            "mainEntity": {
                "@type": "Organization",
                "telephone": "+63 945 356 6294",
            },
        })
        blocks = beacon.extract_json_ld(make_html(ld))
        org = beacon.find_organization(blocks)
        self.assertIsNotNone(org)
        self.assertEqual(org["telephone"], "+63 945 356 6294")

    def test_returns_none_when_absent(self):
        blocks = beacon.extract_json_ld(make_html(json.dumps({"@type": "WebPage"})))
        self.assertIsNone(beacon.find_organization(blocks))


class TestNormalizeDays(unittest.TestCase):
    def test_full_names(self):
        self.assertEqual(
            beacon._normalize_days(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
            frozenset({"Mon", "Tue", "Wed", "Thu", "Fri"}),
        )

    def test_range(self):
        self.assertEqual(beacon._normalize_days("Mon-Fri"), frozenset({"Mon", "Tue", "Wed", "Thu", "Fri"}))

    def test_comma_joined(self):
        self.assertEqual(
            beacon._normalize_days("Monday, Tuesday, Wednesday"),
            frozenset({"Mon", "Tue", "Wed"}),
        )

    def test_weekend(self):
        self.assertEqual(beacon._normalize_days(["Saturday", "Sunday"]), frozenset({"Sat", "Sun"}))


class TestChecks(unittest.TestCase):
    def setUp(self):
        self.expected = beacon.load_config()

    def test_check_nap_match_pass(self):
        org = beacon.find_organization(beacon.extract_json_ld(make_html()))
        result = beacon.check_nap_match(org, self.expected)
        self.assertTrue(result.passed)

    def test_check_nap_match_fail(self):
        org = beacon.find_organization(beacon.extract_json_ld(make_html()))
        org["address"]["postalCode"] = "9999"
        result = beacon.check_nap_match(org, self.expected)
        self.assertFalse(result.passed)
        self.assertEqual(result.severity, "high")

    def test_check_phone_pass(self):
        org = beacon.find_organization(beacon.extract_json_ld(make_html()))
        result = beacon.check_phone(org["telephone"], self.expected)
        self.assertTrue(result.passed)

    def test_check_hours_pass(self):
        schema_hours = beacon.extract_schema_contact(
            beacon.find_organization(beacon.extract_json_ld(make_html()))
        ).hours
        result = beacon.check_hours(schema_hours, self.expected)
        self.assertTrue(result.passed)

    def test_check_hasmap_pass(self):
        org = beacon.find_organization(beacon.extract_json_ld(make_html()))
        result = beacon.check_hasmap(org, self.expected)
        self.assertTrue(result.passed)

    def test_check_sameas_pass(self):
        org = beacon.find_organization(beacon.extract_json_ld(make_html()))
        result = beacon.check_sameas(org)
        self.assertTrue(result.passed)

    def test_check_sameas_missing_google(self):
        org = beacon.find_organization(beacon.extract_json_ld(make_html()))
        org["sameAs"] = ["https://www.linkedin.com/in/example"]
        result = beacon.check_sameas(org)
        self.assertFalse(result.passed)


class TestFooterExtraction(unittest.TestCase):
    def test_extracts_footer_address(self):
        self.assertEqual(
            beacon.extract_footer_address(make_html()),
            "Salinas Dr, Ucma Village, 6000 Cebu",
        )

    def test_empty_when_absent(self):
        self.assertEqual(beacon.extract_footer_address("<html><body></body></html>"), "")


class TestScoring(unittest.TestCase):
    def test_perfect_score(self):
        results = [
            beacon.CheckResult("a", True, "", "high"),
            beacon.CheckResult("b", True, "", "medium"),
        ]
        self.assertEqual(beacon.calculate_score(results), 100)

    def test_critical_failure(self):
        results = [
            beacon.CheckResult("a", False, "", "high"),
        ]
        self.assertEqual(beacon.calculate_score(results), 75)


if __name__ == "__main__":
    unittest.main()
