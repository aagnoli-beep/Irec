from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IREC_", env_file=".env", extra="ignore")

    # In production l'assenza di jwks_url o database_url blocca lo startup.
    # Literal, non str: un typo ("prod", "Production") deve fallire allo
    # startup, non finire nel ramo dev con i mock attivi e la RLS spenta.
    environment: Literal["dev", "production"] = "dev"

    # Auth: JWKS pubblico con cui IREC verifica i call-token firmati da Mind
    # (Mind firma con la chiave privata; IREC detiene solo la parte pubblica).
    jwks_url: str | None = None
    token_audience: str = "irec"

    log_level: str = "INFO"

    # Postgres (da M1 in poi).
    database_url: str | None = None

    # Provider dei microservizi esterni: "mock" (sviluppo) | "reali" (M8).
    # In production i mock bloccano lo startup.
    providers: Literal["mock", "reali"] = "mock"

    # Destinatario della mail di escalation a T+45 (PRD 4.9, "mail a IREC").
    escalation_email_recupero: str = "recupero.crediti@irec.example"


@lru_cache
def get_settings() -> Settings:
    return Settings()
