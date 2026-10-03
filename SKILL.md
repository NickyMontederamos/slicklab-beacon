---
name: slicklab-beacon
version: 0.1.0
description: "Local SEO / AI visibility monitoring."
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [seo, ai-discovery, llm, monitoring, local-business]
---

# SlickLab Beacon — Local SEO & AI Visibility Monitor

## Overview

Beacon is a verification tool that checks `slicklab.digital` (or any site) for NAP consistency, schema completeness, and Google AI visibility signals. It follows the contact-details infrastructure I deployed and ensures your structured data remains valid for AI search discovery.

## Core Features

### 1. NAP Consistency Checker
- Validates Name, Address, Phone across: JSON-LD, footer, llms.txt, contact page
- Flags any mismatch against Google Business Profile
- Outputs pass/fail with exact diff locations

### 2. Schema.org Validator
- Parses all JSON-LD blocks on the page
- Verifies Organization node has:
  - `streetAddress`, `postalCode`, `telephone`
  - `openingHoursSpecification`
  - `hasMap` pointing to Google listing
  - `sameAs` including Google and LinkedIn URLs

### 3. llms.txt Comparator
- Reads llms.txt "Key facts" section
- Compares address, phone, hours against Google listing
- Warns if any field diverges

### 4. AI Visibility Health Score
- Combines NAP match + schema validity + llms.txt consistency
- Returns 0-100 score with recommendations

## When to Use
- Before major content updates (ensure SEO signals don't drift)
- After contact details changes (verify all locations updated)
- Weekly monitoring (CI job or cron)
- Validating new page deployments

## Prerequisites
- Site accessible at public URL
- Google Business Profile verified (URL needed)
- Python 3.14+ with `requests`, `beautifulsoup4` packages

## How to Run

```bash
# Check local site
hermes run "check-local-seo"

# Check with custom domain
hermes run "check-seo: target=https://example.com"

# Run as cron (daily 6am)
hermes cron create --name seo-beacon --deliver telegram "0 6 * * *" "check-local-seo"
```

## Quick Reference

| Check | Passes If |
|-------|-----------|
| NAP match | Address text identical across all 4 locations |
| Schema valid | JSON-LD parses, Organization node complete |
| llms.txt match | Key facts section matches Google listing |
| Hours match | Opening hours identical in schema + llms.txt |

## Procedure

1. Fetch page HTML (with BeautifulSoup)
2. Extract JSON-LD blocks, parse into dict
3. Find Organization node in @graph
4. Read footer address via CSS selectors
5. Read llms.txt key facts section
6. Compare all values against stored Google listing data
7. Output report with any mismatches

## Files in Repo

```
slicklab-beacon/
├── beacon.py          # Main checker script
├── gbp.py             # Google Business Profile API client
├── config.yaml        # Site URLs, GBP CID, expected values
├── SKILL.md           # This file (documentation)
└── reports/           # Output JSON reports
```

## Configuration

```yaml
site_url: https://slicklab.digital
gbp_cid: "10606483454430947215"  # Google Business Profile CID
expected:
  address: "Salinas Dr, Ucma Village, 6000 Cebu"
  phone: "+63 945 356 6294"
  hours:
    - { day: "Mon-Fri", opens: "07:00", closes: "19:00" }
    - { day: "Sat-Sun", opens: "07:00", closes: "15:00" }
```

## Verification

```bash
python3 beacon.py --target slicklab.digital
# Expected: all checks pass, score >= 100
```