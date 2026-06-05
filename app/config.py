"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Devin
    devin_api_key: str = "apk_replace_me"
    devin_api_base: str = "https://api.devin.ai"

    # GitHub
    github_token: str = "ghp_replace_me"
    github_repo: str = "your-org/superset"

    # Pipeline behavior
    scanner_interval_seconds: int = 3600
    scanner_requirements_paths: str = "requirements/base.txt"
    poll_interval_seconds: int = 30
    max_acu_limit: int = 10
    scanner_requires_approval: bool = False

    # Storage
    database_path: str = "./data/pipeline.db"

    @property
    def requirements_paths(self) -> list[str]:
        return [p.strip() for p in self.scanner_requirements_paths.split(",") if p.strip()]

    @property
    def configured(self) -> bool:
        """True once real credentials have been supplied."""
        return (
            self.devin_api_key.startswith("apk_")
            and not self.github_token.startswith("ghp_replace")
            and "/" in self.github_repo
            and not self.github_repo.startswith("your-org")
        )


settings = Settings()
