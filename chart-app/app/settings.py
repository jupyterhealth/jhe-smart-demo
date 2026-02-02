from __future__ import annotations

import secrets

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load settings from environment"""

    model_config = SettingsConfigDict(env_file=".env")

    app_host: str = "http://127.0.0.1:8000"
    secret_key: str = secrets.token_hex(32)
    fhir_client_id: str
    fhir_api_base: str

    jhe_url: str
    jhe_client_id: str
    jhe_public_url: str = ""

    @field_validator("jhe_public_url", mode="before")
    @classmethod
    def _public_url(
        cls,
        v: str,
        values,
    ) -> str:
        if not v:
            return values.data["jhe_url"]
        return v

    @computed_field
    def fhir_redirect_uri(self) -> str:
        return f"{self.app_host}/callback"

    @computed_field
    def jhe_redirect_uri(self) -> str:
        return f"{self.app_host}/jhe_callback"


settings = Settings()
