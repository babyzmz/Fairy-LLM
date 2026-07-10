from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def to_sync_postgres_dsn(dsn: str) -> str:
    url = make_url(dsn)
    if url.drivername == "postgresql+psycopg":
        return url.render_as_string(hide_password=False)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("FAIRY_POSTGRES_DSN must use postgresql+asyncpg")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


class CloudSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAIRY_", extra="ignore")

    postgres_dsn: str
    core_data_dir: Path = Path("/var/lib/fairy/core")

    oidc_issuer: str
    oidc_audience: str = "fairy-cloud"
    oidc_allow_insecure_http: bool = False

    s3_endpoint: str
    s3_bucket: str = "fairy-objects"
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_region: str = "us-east-1"

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8080, ge=1, le=65_535)

    worker_owner_id: str = "fairy-cloud-worker"
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_heartbeat_path: Path = Path("/tmp/fairy-worker-ready")

    @property
    def core_postgres_dsn(self) -> str:
        return to_sync_postgres_dsn(self.postgres_dsn)
