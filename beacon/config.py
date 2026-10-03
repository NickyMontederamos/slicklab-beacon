"""Runtime settings, read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DEFAULT_MODEL = "claude-opus-5-5"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    profiles_dir: Path
    model: str
    llm: str
    secret_key: str
    https: bool
    places_api_key: str | None

    def client_dir(self, client_id: str) -> Path:
        validate_client_id(client_id)
        return self.data_dir / "clients" / client_id

    @property
    def auth_db(self) -> Path:
        return self.data_dir / "auth.db"


def validate_client_id(client_id: str) -> str:
    if not CLIENT_ID_RE.match(client_id or ""):
        raise ValueError(
            f"Invalid client id {client_id!r}: use lowercase letters, digits and hyphens."
        )
    return client_id


def get_settings() -> Settings:
    _load_dotenv(Path.cwd() / ".env")
    return Settings(
        data_dir=Path(os.environ.get("BEACON_DATA_DIR", "./data")).resolve(),
        profiles_dir=Path(os.environ.get("BEACON_PROFILES_DIR", "./profiles")).resolve(),
        model=os.environ.get("BEACON_MODEL") or DEFAULT_MODEL,
        llm=os.environ.get("BEACON_LLM") or "claude",
        secret_key=os.environ.get("BEACON_SECRET_KEY", ""),
        https=os.environ.get("BEACON_HTTPS", "0") == "1",
        places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY") or None,
    )
