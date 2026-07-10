from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.application.service import CoreMethodNotFoundError
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.transports.stdio import build_local_service


def test_core_service_owns_validation_handlers_and_response_serialization(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        health = service.invoke("health", {})
        project = service.invoke(
            "projects.create",
            {"name": "Service project", "residency": "local_only"},
        )

        assert health == {
            "status": "ok",
            "service": "fairy-core",
            "protocol": "core-service-v1",
        }
        assert project["project"]["name"] == "Service project"
        assert isinstance(project["project"]["id"], str)
    finally:
        service.close()


def test_core_service_rejects_unknown_methods_and_invalid_params(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(CoreMethodNotFoundError, match=r"shell\.execute"):
            service.invoke("shell.execute", {})
        with pytest.raises(ValidationError):
            service.invoke("projects.get", {"project_id": "not-a-uuid"})
    finally:
        service.close()


def test_core_method_catalog_is_the_single_public_method_authority() -> None:
    assert set(CORE_METHODS) == {
        "approvals.decide",
        "capabilities.get",
        "changesets.propose",
        "conversations.create",
        "events.subscribe",
        "health",
        "projects.create",
        "projects.get",
        "projects.import",
        "tasks.create",
        "tasks.get",
        "tasks.review",
        "versions.accept",
        "versions.discard",
        "versions.get",
    }
    assert all(method.name == name for name, method in CORE_METHODS.items())
