"""Publishing = exporting approved drafts as ready-to-deploy files.

The gate is enforced here, not in the UI: every draft must be approved, unchanged since that
approval, and the profile must not be regulated. Beacon never posts to third-party sites.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .policy import PolicyError, check_publishable
from .profile import Profile
from .store import Store, content_hash


def publish(profile: Profile, store: Store, out_root: Path, actor: str) -> Path:
    drafts = store.list_drafts("approved")
    if not drafts:
        raise PolicyError("Nothing to publish: no approved drafts.")
    for d in drafts:
        check_publishable(
            profile, d, store.latest_approval(d["id"]), content_hash(d["title"], d["body"])
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_root / stamp
    out.mkdir(parents=True, exist_ok=False)

    faqs = [d for d in drafts if d["kind"] == "faq"]
    explainers = [d for d in drafts if d["kind"] == "explainer"]
    md = [f"# {profile.name} - FAQ\n"]
    for d in faqs:
        md.append(f"## {d['title']}\n\n{d['body']}\n")
    (out / "faq.md").write_text("\n".join(md), encoding="utf-8")
    for d in explainers:
        (out / f"explainer-{d['id']}.md").write_text(
            f"# {d['title']}\n\n{d['body']}\n", encoding="utf-8"
        )
    if faqs:
        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": d["title"],
                    "acceptedAnswer": {"@type": "Answer", "text": d["body"]},
                }
                for d in faqs
            ],
        }
        (out / "faq-schema.jsonld").write_text(
            json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    manifest = []
    for d in drafts:
        approval = store.latest_approval(d["id"])
        manifest.append(
            {
                "draft_id": d["id"],
                "title": d["title"],
                "approved_by": approval["actor"],
                "approved_at": approval["at"],
                "content_hash": approval["content_hash"],
            }
        )
        store.decide(d["id"], "publish", actor, note=f"exported to {out.name}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    store.log(actor, "publish.export", {"dir": str(out), "draft_ids": [d["id"] for d in drafts]})
    return out
