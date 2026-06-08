from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
