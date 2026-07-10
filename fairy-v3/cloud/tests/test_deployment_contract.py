from __future__ import annotations

from pathlib import Path

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory

CLOUD_ROOT = Path(__file__).parents[1]


def test_alembic_has_one_linear_cloud_schema_head() -> None:
    config = Config(CLOUD_ROOT / "alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["20260710_0001"]
    assert scripts.get_revision("20260710_0001").down_revision is None


def test_compose_uses_supported_brokerless_development_services() -> None:
    composition = yaml.safe_load((CLOUD_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = composition["services"]

    assert set(services) == {
        "api",
        "integration",
        "migrate",
        "object-store",
        "oidc",
        "postgres",
        "worker",
    }
    assert services["postgres"]["image"] == "postgres:18.4-alpine3.24"
    assert services["object-store"]["image"] == "chrislusf/seaweedfs:4.39"
    assert services["oidc"]["image"] == "ghcr.io/navikt/mock-oauth2-server:4.0.0"
    assert "fairy-postgres:/var/lib/postgresql" in services["postgres"]["volumes"]
    assert services["object-store"]["environment"]["S3_BUCKET"] == "fairy-objects"
    assert all(
        "healthcheck" in services[name]
        for name in {"api", "object-store", "oidc", "postgres", "worker"}
    )
    assert services["api"]["build"]["target"] == "runtime"
    assert services["worker"]["read_only"] is True
    assert services["worker"]["cap_drop"] == ["ALL"]
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["integration"]["profiles"] == ["test"]
    assert not ({"redis", "nats"} & set(services))
    assert "docker.sock" not in (CLOUD_ROOT / "compose.yaml").read_text(encoding="utf-8")


def test_cloud_image_is_pinned_and_runs_as_non_root() -> None:
    dockerfile = (CLOUD_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
