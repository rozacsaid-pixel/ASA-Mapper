"""Configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SHEET_ID = "1vXaCq5YjwfnUw_OsPilJ5WrXe5JHaFPZoKD4XejfhXk"


@dataclass
class Settings:
    data_dir: Path = Path("data")
    sheet_id: str = DEFAULT_SHEET_ID
    sheet_tab: str = "ASA List"
    service_account: str = ""  # JSON text or path to the service account key file
    google_client_id: str = ""  # sign-in is enabled when this is set
    google_client_secret: str = ""
    allowed_domain: str = "anduintransact.com"
    session_secret: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            # Vercel's filesystem is read-only except /tmp (which is not persistent).
            data_dir=Path(env("ASA_DATA_DIR", "/tmp/asa-data" if env("VERCEL") else "data")),
            sheet_id=env("ASA_SHEET_ID", DEFAULT_SHEET_ID),
            sheet_tab=env("ASA_SHEET_TAB", "ASA List"),
            service_account=env("ASA_GOOGLE_SERVICE_ACCOUNT", ""),
            google_client_id=env("ASA_GOOGLE_CLIENT_ID", ""),
            google_client_secret=env("ASA_GOOGLE_CLIENT_SECRET", ""),
            allowed_domain=env("ASA_ALLOWED_DOMAIN", "anduintransact.com"),
            session_secret=env("ASA_SESSION_SECRET", ""),
        )
