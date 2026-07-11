from __future__ import annotations

import json
import struct
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fairy_core.runtime.models import ExecutorRuntimeState, RuntimeProbeResult
from fairy_core.runtime.review import (
    BrowserCapture,
    RuntimeHealthCheck,
    RuntimeReviewExecutorHealth,
    RuntimeReviewRequest,
)

from fairy_cloud.runtime.models import CloudRuntimeReviewBinding
from fairy_cloud.runtime.review import CloudRuntimeReviewer, CloudRuntimeReviewWire
from fairy_cloud.workers.runtime import RuntimeTargetResolver, create_runtime_worker_app

GATEWAY_KEY = "r" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 1280, 720)


@pytest.mark.asyncio
async def test_cloud_runtime_reviewer_uses_private_bounded_gateway_contract() -> None:
    requests: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-fairy-runtime-gateway-key"] == GATEWAY_KEY
        if request.url.path == "/internal-review/health":
            return httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "http_available": True,
                    "browser_available": True,
                    "diagnostics": ["fixture"],
                },
            )
        wire = json.loads(request.content)
        assert wire["schema_version"] == 1
        if request.url.path.endswith("/health"):
            return httpx.Response(
                200,
                json={
                    "status_code": 200,
                    "latency_ms": 7,
                    "content_type": "text/html",
                    "body_sha256": "a" * 64,
                    "body_bytes": 42,
                },
            )
        return httpx.Response(
            200,
            content=PNG,
            headers={
                "content-type": "image/png",
                "x-fairy-image-width": "1280",
                "x-fairy-image-height": "720",
            },
        )

    reviewer = CloudRuntimeReviewer(
        gateway_base_url="http://runtime:8082",
        gateway_key=GATEWAY_KEY,
        client=httpx.Client(transport=httpx.MockTransport(gateway)),
    )
    request = _request()

    health = reviewer.health()
    checked = reviewer.check(request)
    capture = reviewer.capture(request)

    assert health.http_available is True and health.browser_available is True
    assert checked.status_code == 200
    assert capture.png == PNG
    assert capture.width == 1280 and capture.height == 720
    assert [item.url.path for item in requests] == [
        "/internal-review/health",
        f"/internal-review/{request.runtime_id}/health",
        f"/internal-review/{request.runtime_id}/capture",
    ]


@pytest.mark.asyncio
async def test_runtime_worker_review_api_requires_key_and_returns_typed_evidence(
    tmp_path: Path,
) -> None:
    request = _request()
    wire = CloudRuntimeReviewWire.from_request(request)

    class Resolver:
        async def resolve_internal(self, _runtime_id: str) -> str:
            return "http://127.0.0.1:43125/"

        async def resolve_review(self, observed: CloudRuntimeReviewWire) -> str:
            assert observed == wire
            return "http://127.0.0.1:43125/"

    class Reviewer:
        def health(self) -> RuntimeReviewExecutorHealth:
            return RuntimeReviewExecutorHealth(
                executor="fixture",
                version="1",
                http_available=True,
                browser_available=True,
                diagnostics=("fixture",),
            )

        def check(self, observed: RuntimeReviewRequest) -> RuntimeHealthCheck:
            assert observed.execution_target == "local"
            return RuntimeHealthCheck(
                status_code=200,
                latency_ms=1,
                content_type="text/html",
                body_sha256="b" * 64,
                body_bytes=2,
            )

        def capture(self, observed: RuntimeReviewRequest) -> BrowserCapture:
            assert observed.url == "http://127.0.0.1:43125/"
            return BrowserCapture(
                png=PNG,
                width=1280,
                height=720,
                device_scale_factor=1.0,
            )

    app = create_runtime_worker_app(
        worker=object(),  # Lifespan is not entered by the ASGI test transport.
        resolver=Resolver(),
        poll_seconds=0.1,
        heartbeat_path=tmp_path / "heartbeat",
        gateway_key=GATEWAY_KEY,
        reviewer=Reviewer(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://runtime:8082",
    ) as client:
        denied = await client.get("/internal-review/health")
        health = await client.get(
            "/internal-review/health",
            headers={"X-Fairy-Runtime-Gateway-Key": GATEWAY_KEY},
        )
        checked = await client.post(
            f"/internal-review/{request.runtime_id}/health",
            headers={"X-Fairy-Runtime-Gateway-Key": GATEWAY_KEY},
            json=wire.model_dump(mode="json"),
        )
        captured = await client.post(
            f"/internal-review/{request.runtime_id}/capture",
            headers={"X-Fairy-Runtime-Gateway-Key": GATEWAY_KEY},
            json=wire.model_dump(mode="json"),
        )

    assert denied.status_code == 404
    assert health.json()["browser_available"] is True
    assert checked.json()["body_sha256"] == "b" * 64
    assert captured.content == PNG
    assert captured.headers["x-fairy-image-width"] == "1280"


@pytest.mark.asyncio
async def test_runtime_review_resolver_rejects_scope_rebinding() -> None:
    request = _request()
    wire = CloudRuntimeReviewWire.from_request(request)

    class Store:
        async def active_review_binding(self, _runtime_id):
            return CloudRuntimeReviewBinding(
                runtime_id=wire.runtime_id,
                preview_id=wire.preview_id,
                project_id=wire.project_id,
                conversation_id=wire.conversation_id,
                task_id=wire.task_id,
                version_id=wire.version_id,
                scope_digest=wire.scope_digest,
                workspace_generation=wire.workspace_generation,
            )

        async def active_handle(self, runtime_id):
            return f"cloud-dynamic:{runtime_id}:1"

    class Supervisor:
        def probe(self, handle: str):
            return RuntimeProbeResult(
                executor_handle=handle,
                state=ExecutorRuntimeState.RUNNING,
                host="127.0.0.1",
                port=43125,
                url="http://127.0.0.1:43125/",
            )

    resolver = RuntimeTargetResolver(store=Store(), supervisor=Supervisor())

    assert await resolver.resolve_review(wire) == "http://127.0.0.1:43125/"
    with pytest.raises(ValueError, match="binding"):
        await resolver.resolve_review(wire.model_copy(update={"task_id": uuid4()}))


def _request() -> RuntimeReviewRequest:
    return RuntimeReviewRequest(
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        runtime_id=uuid4(),
        preview_id=uuid4(),
        preview_manifest_id=uuid4(),
        workspace_generation=3,
        scope_digest="c" * 64,
        execution_target="cloud",
        url=f"https://{'a' * 52}.preview.fairy.test/",
    )
