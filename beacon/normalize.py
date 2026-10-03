"""Small helpers for comparing names, phones, addresses and hours across sources."""

from __future__ import annotations

import re
import unicodedata

ABBREVIATIONS = {
    "dr": "drive",
    "st": "street",
    "ave": "avenue",
    "av": "avenue",
    "rd": "road",
    "blvd": "boulevard",
    "brgy": "barangay",
    "bgy": "barangay",
    "vill": "village",
}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def phone_digits(phone: str | None, default_country: str = "63") -> str:
    """Normalise to country code + national number: '+63 945 356 6294' and '0945-356-6294'
    both become '639453566294'."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country + digits[1:]
    return digits


PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


def phones_in(text: str) -> set[str]:
    return {phone_digits(m) for m in PHONE_RE.findall(text or "") if len(re.sub(r"\D", "", m)) >= 9}


def name_tokens(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold(text))


def names_match(a: str, candidates: list[str]) -> bool:
    na = name_tokens(a)
    return any(na and na == name_tokens(c) for c in candidates)


def address_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", fold(text))
    return {ABBREVIATIONS.get(w, w) for w in words}


def address_match(expected_street: str, expected_postal: str | None, found: str) -> bool:
    """True when every significant street token and the postal code appear in `found`."""
    want = {t for t in address_tokens(expected_street) if len(t) > 1}
    have = address_tokens(found)
    if not want <= have:
        return False
    return not expected_postal or expected_postal in have


def mentions(text: str, names: list[str], extra: list[str] | None = None) -> list[str]:
    """Which of the business's identifiers appear in `text`."""
    hay = fold(text)
    squashed = name_tokens(text)
    found = []
    for n in names:
        fn = fold(n)
        if not fn:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(fn) + r"(?![a-z0-9])"
        if re.search(pattern, hay) or (len(name_tokens(n)) >= 6 and name_tokens(n) in squashed):
            found.append(n)
    for e in extra or []:
        if e and fold(e) in hay:
            found.append(e)
    return found
