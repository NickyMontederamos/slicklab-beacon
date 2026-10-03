"""Business profile: one YAML file per client, the single source of truth for facts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .config import validate_client_id

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


class Address(BaseModel):
    street: str
    locality: str
    region: str | None = None
    postal_code: str | None = None
    country: str = "PH"

    def one_line(self) -> str:
        parts = [self.street, self.locality, self.region, self.postal_code, self.country]
        return ", ".join(p for p in parts if p)


class Contact(BaseModel):
    phone: str | None = None
    email: str | None = None
    address: Address | None = None
    google_maps_url: str | None = None
    google_place_id: str | None = None


class HoursRule(BaseModel):
    days: list[str]
    opens: str
    closes: str

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d not in DAYS]
        if bad:
            raise ValueError(f"Unknown day codes {bad}; use {DAYS}")
        return v


class Service(BaseModel):
    name: str
    description: str = ""


class Fact(BaseModel):
    id: str
    text: str
    source: str = "owner"


class Consent(BaseModel):
    granted_by: str
    date: date
    method: str = Field(description="e.g. 'signed letter', 'email'")
    reference: str | None = Field(default=None, description="Where the written consent is filed")


class VisibilityConfig(BaseModel):
    questions: list[str] = Field(default_factory=list)
    runs_per_question: int = 3
    location_hint: str | None = None

    @field_validator("questions")
    @classmethod
    def _max_questions(cls, v: list[str]) -> list[str]:
        if len(v) > 20:
            raise ValueError("Keep visibility questions to 20 or fewer.")
        return v


class Profile(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    category: str
    website: str | None = None
    regulated: bool = False
    owner_is_operator: bool = False
    consent: Consent | None = None
    contact: Contact = Field(default_factory=Contact)
    hours: list[HoursRule] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    tone: str = "Clear, friendly, plain English."
    facts: list[Fact] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    visibility: VisibilityConfig = Field(default_factory=VisibilityConfig)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return validate_client_id(v)

    @model_validator(mode="after")
    def _checks(self) -> Profile:
        if not self.owner_is_operator and self.consent is None:
            raise ValueError(
                f"Profile {self.id!r}: written consent is required for businesses the operator "
                "does not own. Add a `consent:` block (granted_by, date, method, reference)."
            )
        ids = [f.id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Profile {self.id!r}: fact ids must be unique.")
        return self

    # -- helpers -----------------------------------------------------------------

    @property
    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    def fact_sheet(self) -> list[Fact]:
        """Facts as the agents see them: explicit facts plus ones derived from structured fields."""
        derived: list[Fact] = [Fact(id="p-name", text=f"Business name: {self.name}")]
        derived.append(Fact(id="p-category", text=f"Category: {self.category}"))
        if self.website:
            derived.append(Fact(id="p-website", text=f"Website: {self.website}"))
        if self.contact.phone:
            derived.append(Fact(id="p-phone", text=f"Phone: {self.contact.phone}"))
        if self.contact.email:
            derived.append(Fact(id="p-email", text=f"Email: {self.contact.email}"))
        if self.contact.address:
            derived.append(Fact(id="p-address", text=f"Address: {self.contact.address.one_line()}"))
        for i, h in enumerate(self.hours):
            derived.append(
                Fact(id=f"p-hours-{i}", text=f"Open {'/'.join(h.days)} {h.opens}-{h.closes}")
            )
        for i, s in enumerate(self.services):
            text = f"Service: {s.name}" + (f" - {s.description}" if s.description else "")
            derived.append(Fact(id=f"p-service-{i}", text=text))
        return derived + list(self.facts)

    def fact_text(self) -> str:
        return "\n".join(f"[{f.id}] {f.text}" for f in self.fact_sheet())


def load_profile(path: Path) -> Profile:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)


def find_profile(profiles_dir: Path, client_id: str) -> Profile:
    validate_client_id(client_id)
    for ext in (".yaml", ".yml"):
        p = profiles_dir / f"{client_id}{ext}"
        if p.is_file():
            profile = load_profile(p)
            if profile.id != client_id:
                raise ValueError(f"{p.name}: id {profile.id!r} does not match file name.")
            return profile
    raise FileNotFoundError(f"No profile for {client_id!r} in {profiles_dir}")


def list_profiles(profiles_dir: Path) -> list[Profile]:
    out = []
    for p in sorted(profiles_dir.glob("*.y*ml")):
        if p.name.startswith("_"):
            continue
        out.append(load_profile(p))
    return out
