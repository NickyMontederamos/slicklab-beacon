import pytest
from pydantic import ValidationError

from beacon.profile import Profile, find_profile
from tests.conftest import PROFILE


def test_consent_required_for_third_party():
    with pytest.raises(ValidationError, match="consent"):
        Profile.model_validate({**PROFILE, "owner_is_operator": False})


def test_fact_sheet_includes_structured_fields(profile):
    ids = {f.id for f in profile.fact_sheet()}
    assert {"p-name", "p-phone", "p-address", "p-hours-0", "f-1"} <= ids


def test_bad_client_id_rejected():
    with pytest.raises(ValidationError):
        Profile.model_validate({**PROFILE, "id": "../etc"})


def test_too_many_questions():
    with pytest.raises(ValidationError):
        Profile.model_validate({**PROFILE, "visibility": {"questions": ["q"] * 21}})


def test_shipped_profiles_are_valid():
    from pathlib import Path

    p = find_profile(Path(__file__).parent.parent / "profiles", "slicklab-digital")
    assert p.contact.phone == "+63 945 356 6294"
