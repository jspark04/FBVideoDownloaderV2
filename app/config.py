from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_token: str                                  # required
    cookies_path: str = "/config/cookies.txt"
    download_dir: str = "/downloads"
    impersonate_target: str = "chrome"
    ntfy_url: str = ""
    canary_url: str = ""
    notify_on_success: bool = True
    port: int = 8080

    @field_validator("auth_token")
    @classmethod
    def _auth_token_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("AUTH_TOKEN must not be empty")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
