from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, model_validator
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
    event_poll_seconds: float = Field(default=0.025, gt=0, le=0.1)
    recovery_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    preview_base_url: str | None = None
    preview_signing_key: SecretStr | None = None
    runtime_gateway_key: SecretStr | None = None
    mcp_credentials: dict[str, SecretStr] = Field(default_factory=dict)
    mcp_allowed_hosts: tuple[str, ...] = ()

    worker_owner_id: str = "fairy-cloud-worker"
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_heartbeat_path: Path = Path("/tmp/fairy-worker-ready")

    @model_validator(mode="after")
    def validate_preview_configuration(self) -> CloudSettings:
        configured = self.preview_base_url is not None
        has_key = self.preview_signing_key is not None
        has_gateway_key = self.runtime_gateway_key is not None
        if len({configured, has_key, has_gateway_key}) != 1:
            raise ValueError(
                "FAIRY_PREVIEW_BASE_URL, FAIRY_PREVIEW_SIGNING_KEY and "
                "FAIRY_RUNTIME_GATEWAY_KEY must be configured together"
            )
        if (
            self.preview_signing_key is not None
            and len(self.preview_signing_key.get_secret_value().encode("utf-8")) < 32
        ):
            raise ValueError("FAIRY_PREVIEW_SIGNING_KEY must contain at least 32 bytes")
        if (
            self.runtime_gateway_key is not None
            and len(self.runtime_gateway_key.get_secret_value().encode("utf-8")) < 32
        ):
            raise ValueError("FAIRY_RUNTIME_GATEWAY_KEY must contain at least 32 bytes")
        return self

    @property
    def core_postgres_dsn(self) -> str:
        return to_sync_postgres_dsn(self.postgres_dsn)


class OutboxWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAIRY_", extra="ignore")

    postgres_dsn: str
    worker_owner_id: str = "fairy-cloud-worker"
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_heartbeat_path: Path = Path("/tmp/fairy-worker-ready")


class RuntimeWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAIRY_", extra="ignore")

    postgres_dsn: str
    runtime_owner_id: str = "fairy-runtime-1"
    runtime_poll_seconds: float = Field(default=0.1, gt=0, le=10)
    runtime_lease_seconds: int = Field(default=180, ge=150, le=600)
    runtime_heartbeat_path: Path = Path("/tmp/fairy-runtime-ready")
    runtime_gateway_base_url: str = "http://runtime:8082"
    runtime_gateway_key: SecretStr
    runtime_host: str = "0.0.0.0"
    runtime_port: int = Field(default=8082, ge=1, le=65_535)
