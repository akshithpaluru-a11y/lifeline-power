"""Runtime settings, read from environment variables (and an optional `.env` file)."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All configuration for the service.

    Every field can be set with an environment variable named `LIFELINE_<FIELD>` (for example
    `LIFELINE_DB=/data/lifeline.db`), except the Anthropic settings, which use the SDK's usual
    `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` names.
    """

    model_config = SettingsConfigDict(env_prefix="LIFELINE_", env_file=".env", extra="ignore")

    db: Path = BACKEND_DIR / "lifeline.db"
    cors_origins: str = "*"
    """Comma-separated list of allowed origins; `*` allows any origin (fine for development)."""

    ticker_enabled: bool = True
    ticker_interval_seconds: float = Field(default=2.0, gt=0)
    sse_ping_seconds: float = Field(default=15.0, gt=0)

    # Limits on wrong join codes, so codes can't be guessed (see `auth.JoinCodeGuard`).
    join_attempts_per_client: int = Field(default=10, ge=1)
    """Wrong join codes one IP address (or IPv6 /64) may try per window."""
    join_attempts_overall: int = Field(default=200, ge=1)
    """Wrong join codes everyone together may try per window, against guessing from many
    addresses at once."""
    join_attempt_window_seconds: float = Field(default=900.0, gt=0)

    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key")
    )
    anthropic_model: str = Field(
        default="claude-haiku-5-5",
        validation_alias=AliasChoices("ANTHROPIC_MODEL", "anthropic_model"),
    )
    ai_timeout_seconds: float = Field(default=30.0, gt=0)

    overpass_url: str = "https://overpass-api.de/api/interpreter"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    census_geocoder_url: str = "https://geocoding.geo.census.gov/geocoder"
    http_timeout_seconds: float = Field(default=8.0, gt=0)
    user_agent: str = "LifeLinePower/1.0 (Congressional App Challenge project; outage safety app)"

    @property
    def cors_origin_list(self) -> list[str]:
        """The allowed CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for the SQLite database file."""
        return f"sqlite+aiosqlite:///{self.db}"


@lru_cache
def get_settings() -> Settings:
    """Settings for this process, read once from the environment."""
    return Settings()
