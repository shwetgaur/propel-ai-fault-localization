from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/faultloc.db"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    detection_debounce_seconds: float = 8.0
    scheduled_outage_grace_minutes: int = 30
    stale_telemetry_minutes: int = 20
    seed_on_startup: bool = True
    cors_origins: str = "*"


settings = Settings()