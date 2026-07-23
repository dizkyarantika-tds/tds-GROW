from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Refresh policy — governs both the radar/Jira background scheduler and the
    # Data Quality cache TTL. One knob, configurable without touching code.
    refresh_interval_hours: float = 24.0

    # Snowflake
    snowflake_account: str
    snowflake_user: str
    snowflake_role: str
    snowflake_warehouse: str
    snowflake_database: str = "TDS_DB"
    snowflake_private_key_path: str | None = None
    snowflake_private_key_pem: str | None = None
    snowflake_private_key_passphrase: str | None = None

    # Jira
    jira_site: str = "tripledotstudios.atlassian.net"
    jira_email: str
    jira_api_token: str
    jira_projects: str = "AS,DS"

    # Google OAuth
    google_client_id: str
    google_client_secret: str
    google_allowed_domain: str = "tripledotstudios.com"
    oauth_redirect_url: str

    # Session
    session_secret: str

    # App
    port: int = 8000
    cache_db_path: str = "./data/cache.db"

    @property
    def jira_project_list(self) -> list[str]:
        return [p.strip() for p in self.jira_projects.split(",") if p.strip()]

    @property
    def cache_db_full_path(self) -> Path:
        path = Path(self.cache_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
