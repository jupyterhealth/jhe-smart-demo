from __future__ import annotations

import secrets

import requests
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load settings from environment"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_host: str = "http://127.0.0.1:8000"
    secret_key: str = secrets.token_hex(32)
    fhir_client_id: str
    fhir_api_base: str

    jhe_url: str

    @computed_field
    def fhir_redirect_uri(self) -> str:
        return f"{self.app_host}/callback"

    @computed_field
    def fhir_smart_configuration(self) -> str:
        r = requests.get(f"{self.fhir_api_base}/.well-known/smart-configuration")
        r.raise_for_status()
        return r.json()


settings = Settings()
