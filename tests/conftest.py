from __future__ import annotations

import pytest

from beacon.profile import Profile
from beacon.store import Store

PROFILE = {
    "id": "acme",
    "name": "Acme Studio",
    "aliases": ["Acme"],
    "category": "Web studio",
    "website": "https://acme.test",
    "owner_is_operator": True,
    "contact": {
        "phone": "+63 945 356 6294",
        "address": {
            "street": "Salinas Dr, Ucma Village",
            "locality": "Cebu City",
            "region": "Cebu",
            "postal_code": "6000",
            "country": "PH",
        },
    },
    "hours": [
        {"days": ["Mo", "Tu", "We", "Th", "Fr"], "opens": "07:00", "closes": "19:00"},
        {"days": ["Sa", "Su"], "opens": "07:00", "closes": "15:00"},
    ],
    "facts": [{"id": "f-1", "text": "Acme Studio builds websites for schools."}],
    "visibility": {
        "questions": ["Who builds school websites in Cebu?", "Web studio in Cebu?"],
        "runs_per_question": 2,
    },
}


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate(PROFILE)


@pytest.fixture
def regulated() -> Profile:
    data = {
        **PROFILE,
        "id": "atty",
        "name": "Atty. Test",
        "regulated": True,
        "owner_is_operator": False,
        "consent": {"granted_by": "Atty. Test", "date": "2026-10-01", "method": "letter"},
    }
    return Profile.model_validate(data)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "acme")
