# SlickLab Beacon

Beacon builds a business's online presence and its visibility in AI answers, **with a human approving every step**.

You give it a profile (one YAML file of verified facts about a business). Beacon runs agents against it:

| Agent | What it does | Uses an LLM? |
|---|---|---|
| **Audit** | Checks the website, its JSON-LD schema, `llms.txt` and the Google listing all agree on name, address, phone and hours. | No - deterministic, free to run |
| **Content** | Drafts FAQs and explainers using only the profile's facts. | Yes |
| **Critic** | Checks every claim in a draft against the facts. Flags anything unsupported, plus review/impersonation language and phone numbers, prices or years that aren't in the facts. | Yes, plus rule checks |
| **Visibility** | Asks AI assistants ~20 realistic customer questions, several times each, and records how often the business is named. Re-run after changes to see if anything worked. | Yes |

The web page has an **Overview** with a "What to do next" list and buttons for Run audit, Generate drafts and Publish approved (admins only). Drafts land in a web **inbox** (`beacon.slicklab.digital`) where you or the client clicks **Approve**, **Edit** or **Reject**.

## What it won't do (enforced in code, not just prompts)

- **No fake reviews, no pretending to be a customer, no undisclosed affiliation.** The critic blocks testimonial language, star ratings, first-person customer voice and guarantees (`beacon/policy.py`).
- **Nothing publishes without an approval record.** The approval stores a hash of the exact text. Edit it afterwards and it needs approval again (`beacon/publish.py`).
- **Regulated businesses (lawyers, notaries) are draft-only, always.** `regulated: true` makes `publish` refuse. Drafts also need the "not legal advice" line, and outcome promises are blocked.
- **Third-party clients need written consent.** A profile with `owner_is_operator: false` won't load without a `consent:` block.
- **Flagged drafts can't be approved silently.** Approving one requires a note explaining why.

## Quick start (5 minutes, no API key needed)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env               # then set BEACON_SECRET_KEY (see the file)

export BEACON_LLM=fake             # offline demo mode - no API calls, no cost
beacon profiles                    # validate profiles
beacon draft slicklab-digital --count 3
beacon visibility run slicklab-digital --label baseline --runs 2
beacon user add Nicole --role admin   # prints a login token once - save it
beacon serve                       # open http://127.0.0.1:8077 and paste the token
```

Switch to the real model by removing `BEACON_LLM=fake` and setting `ANTHROPIC_API_KEY` in `.env`.

## Day-to-day commands

```bash
beacon audit slicklab-digital                    # free; run after any site change
beacon draft slicklab-digital --count 5 --topic "hours and location"
beacon queue slicklab-digital --status pending   # or review in the web inbox
beacon approve slicklab-digital 3 --by Nicole    # flagged drafts need --note "why"
beacon publish slicklab-digital --by Nicole      # exports faq.md + FAQPage JSON-LD

beacon visibility run slicklab-digital --label baseline     # asks before spending
beacon visibility run slicklab-digital --label after-schema-fix
beacon visibility compare slicklab-digital 1 2
```

**Publish** writes files to `data/clients/<id>/publish/<timestamp>/` (`faq.md`, `faq-schema.jsonld`, `manifest.json` with who approved what). You paste those into the site. Beacon never posts anywhere by itself.

## Free models for visibility runs

Visibility runs use a **free-tier model**, so measuring costs nothing. Drafting and fact-checking still use Claude (free models aren't reliable enough to fact-check).

1. Get a free key from OpenRouter, Groq or Gemini (or run Ollama locally, no key).
2. In `.env`: `BEACON_VISIBILITY_PROVIDER=openrouter` (or `groq`/`gemini`/`ollama`), the matching `*_API_KEY`, and `BEACON_FREE_MODEL`.
3. Not sure which model? `beacon free-models` lists the free OpenRouter ones. Names change often.
4. Click **Run measurement** on the AI visibility tab, or `beacon visibility run <id> --label baseline`.

**Caveat:** a free model only tells you how *that model* answers, not ChatGPT or Gemini. Use the **same model** for before and after. Beacon records the model on every run and warns you if you compare runs from different models.

## Reading the visibility numbers honestly

AI answers vary from run to run. So each question is asked several times (default 3), and `compare` runs a significance test. If it says *"No clear change yet"*, the difference could be noise, so don't sell it as a win. With 12 questions × 3 runs = 36 answers, you need a fairly big jump (roughly 20-25 points) to be sure. Use `--runs 5` for tighter numbers.

`--web` (on by default) lets the model search the web, which is closer to how ChatGPT, Gemini and Claude answer "who should I hire" questions today. `--no-web` measures what the model knows from training alone.

Cost: one run = questions × runs API calls (36 for the default SlickLab profile). The CLI shows the count and asks before starting.

## Adding a client

1. Get **written consent** first. File it, and note where in the profile.
2. Copy `profiles/_template.yaml` (or `_template-regulated.yaml` for lawyers) to `profiles/<client-id>.yaml`.
3. Fill in **only facts the client has confirmed**. The agents can say nothing that isn't in this file.
4. `beacon profiles` to validate. Then `beacon user add "Client Name" --role client --client <client-id>`. Client users only ever see their own profile.

Each client's data lives in its own SQLite file: `data/clients/<client-id>/beacon.db`.

## Deploy on a VPS (beacon.slicklab.digital)

```bash
sudo useradd -r -m -d /opt/beacon beacon
sudo -u beacon git clone https://github.com/NickyMontederamos/slicklab-beacon /opt/beacon
cd /opt/beacon && sudo -u beacon python3 -m venv .venv && sudo -u beacon .venv/bin/pip install .
sudo -u beacon cp .env.example .env && sudo -u beacon nano .env   # set keys, BEACON_HTTPS=1
sudo -u beacon mkdir -p data
sudo cp deploy/beacon.service /etc/systemd/system/ && sudo systemctl enable --now beacon
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
sudo -u beacon .venv/bin/beacon user add Nicole --role admin
```

Optional schedules are in `deploy/crontab.example` (weekly audit, monthly visibility run).
**Back up `data/`.** It holds every approval record.

## Update the live server (one line)

```bash
sudo sh /opt/beacon/deploy/update.sh
```

## Settings (`.env`)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key for content, critic, visibility |
| `BEACON_MODEL` | Default `claude-opus-5-5` |
| `BEACON_LLM` | `claude` or `fake` (offline demo/tests) |
| `BEACON_SECRET_KEY` | 32+ random chars; signs login cookies |
| `BEACON_HTTPS` | `1` in production (secure cookies) |
| `GOOGLE_PLACES_API_KEY` | Optional; lets the audit compare the live Google listing |

Claude calls use the server-side refusal fallback (`fallbacks: "default"`), so a rare safety decline on one model is retried on another inside the same call instead of failing the run.

## Development

```bash
pytest -q && ruff check . && ruff format --check .
```

Layout: `beacon/agents/` (audit, content, critic, visibility), `beacon/policy.py` (guardrails), `beacon/store.py` (per-client SQLite), `beacon/web/` (inbox), `profiles/`, `deploy/`.

## Roadmap

- **Research agent:** monitor questions people ask (forums, Reddit, Google "People also ask").
- **Engagement agent:** draft helpful, clearly disclosed replies for a human to post. Never auto-posted.
- Multi-provider visibility (ChatGPT, Gemini, Perplexity) next to Claude.
