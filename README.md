# SlickLab Beacon

Local SEO / AI visibility monitor for SlickLab.Digital.

Beacon verifies that a site's **NAP** (Name, Address, Phone), **Schema.org** structured data, **llms.txt**, and **Google Business Profile** stay consistent. Google and AI search engines (ChatGPT, Gemini, Perplexity, Google AI Overviews) use NAP consistency and structured data as trust signals — a mismatch is a negative signal for local and AI discovery.

Beacon follows on from the contact-details work shipped on slicklab.digital: it makes sure those details never drift.

## Why it matters

- Google treats consistent NAP across your site and Google Business Profile as a trust signal.
- AI engines extract facts from Schema.org JSON-LD. A malformed or incomplete `Organization` node means your business is invisible to AI answers.
- `llms.txt` is read by LLM crawlers; it must match your listing.

## Features

- **NAP consistency** — validates address components (street, village, postal code, city) against the canonical display address.
- **Phone match** — confirms the schema `telephone` matches the published number.
- **Hours match** — normalizes schema `openingHoursSpecification` (full day names / ranges) against the config and compares.
- **hasMap** — verifies the schema points to your Google Business Profile.
- **sameAs** — confirms LinkedIn and Google profile URLs are present.
- **Footer check** — confirms the footer carries the full address.
- **Health score** — 0–100, weighted by severity (high = −25, medium = −10).

## Requirements

- Python 3.10+
- `requests` (`pip install requests`)
- No other dependencies — HTML parsing uses the Python stdlib `html.parser`.

## Usage

```bash
# Check the live site (default target)
python3 beacon.py

# Check a specific URL
python3 beacon.py --target https://slicklab.digital/contact/

# Save a JSON report
python3 beacon.py --target https://slicklab.digital --output report.json
```

### Exit codes

- `0` — health score ≥ 70
- `1` — health score < 70 or fetch failure

## Configuration

Edit `config.yaml` to set the expected contact details. The defaults match the current SlickLab.Digital Google Business Profile:

```yaml
expected_contact:
  address: "Salinas Dr, Ucma Village, 6000 Cebu"
  phone: "+63 945 356 6294"

opening_hours:
  - day: "Mon-Fri"
    opens: "07:00"
    closes: "19:00"
  - day: "Sat-Sun"
    opens: "07:00"
    closes: "15:00"
```

## How it works

1. Fetches the target page.
2. Extracts every `application/ld+json` block using stdlib `html.parser`.
3. Locates the `Organization` node — standalone, inside `@graph`, or as a page's `mainEntity`.
4. Compares schema components against `config.yaml`.
5. Scans the raw HTML footer for the display address.
6. Prints a pass/fail table and a 0–100 health score.

## Notes

- The homepage carries the canonical, complete `Organization` node (with hours, `hasMap`, `sameAs`). Inner pages reference it by `@id` via `mainEntity`, so they validate NAP and phone but not the homepage-only fields. This is valid Schema.org practice and avoids data duplication.
- Phone is checked in international format (`+63 …`), Google's recommended format for structured data.

## License

MIT