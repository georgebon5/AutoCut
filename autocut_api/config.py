"""API configuration — data directory layout and database URL."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiConfig(BaseSettings):
    """All env vars are prefixed ``AUTOCUT_API_`` (e.g. AUTOCUT_API_DATA_DIR)."""

    # Where uploaded clips, proxies, EDLs and rendered outputs live.
    data_dir: Path = Path("autocut_data")

    # Explicit DB URL overrides the default location under data_dir.
    database_url: str | None = None

    # Fixed chunk size for the resumable upload protocol. The server dictates
    # this so chunk indexing is deterministic across clients.
    upload_chunk_size: int = 5 * 1024 * 1024    # 5 MB

    model_config = SettingsConfigDict(env_prefix="AUTOCUT_API_", extra="ignore")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'autocut.db'}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
