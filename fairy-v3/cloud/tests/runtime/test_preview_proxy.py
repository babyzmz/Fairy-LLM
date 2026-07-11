from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from fairy_cloud.runtime.proxy import CloudPreviewProxy, RuntimeInternalGateway

GATEWAY_KEY = "g" * 32


class FakeRoutes:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def resolve(self, token: str) -> str:
        self.tokens.append(token)
        return "http://runtime:8082/internal/01980f66-b740-7dc8-9e1b-2714cf0c8801/"


@pytest.mark.asyncio
async def test_preview_subdomain_proxy_preserves_root_paths_and_strips_credentials() -> None:
    routes = FakeRoutes()
    upstream_requests: list[httpx.Request] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(
            200,
            content=b"export const ready = true;",
            headers={"content-type": "text/javascript", "server": "private-worker"},
        )

    proxy = CloudPreviewProxy(
        base_domain="preview.fairy.test",
        routes=routes,
        gateway_key=GATEWAY_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    app = FastAPI()
    proxy.install(app)
    token = "a" * 52
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"https://{token}.preview.fairy.test",
    ) as client:
        response = await client.get(
            "/src/main.ts?import=1",
            headers={
                "Authorization": "Bearer must-not-forward",
                "Cookie": "cloud-session=must-not-forward",
                "Accept": "*/*",
                "X-Fairy-Runtime-Gateway-Key": "client-forgery",
            },
        )

    assert response.status_code == 200
    assert response.text == "export const ready = true;"
    assert "server" not in response.headers
    assert routes.tokens == [token]
    forwarded = upstream_requests[0]
    assert str(forwarded.url) == (
        "http://runtime:8082/internal/01980f66-b740-7dc8-9e1b-2714cf0c8801/src/main.ts?import=1"
    )
    assert "authorization" not in forwarded.headers
    assert "cookie" not in forwarded.headers
    assert forwarded.headers["accept"] == "*/*"
    assert forwarded.headers["x-fairy-runtime-gateway-key"] == GATEWAY_KEY


@pytest.mark.asyncio
async def test_preview_proxy_ignores_non_preview_hosts_and_rejects_bad_targets() -> None:
    class BadRoutes:
        def resolve(self, _token: str) -> str:
            return "http://169.254.169.254/latest/meta-data/"

    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    CloudPreviewProxy(
        base_domain="preview.fairy.test",
        routes=BadRoutes(),
        gateway_key=GATEWAY_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)),
    ).install(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://api.fairy.test",
    ) as client:
        assert (await client.get("/health")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"https://{'a' * 52}.preview.fairy.test",
    ) as client:
        response = await client.get("/")
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_internal_gateway_resolves_only_loopback_runtime_endpoint() -> None:
    class Resolver:
        async def resolve_internal(self, runtime_id: str) -> str:
            assert runtime_id == "01980f66-b740-7dc8-9e1b-2714cf0c8801"
            return "http://127.0.0.1:43125/"

    forwarded: list[str] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        forwarded.append(str(request.url))
        return httpx.Response(200, content=b"ok")

    app = FastAPI()
    RuntimeInternalGateway(
        resolver=Resolver(),
        gateway_key=GATEWAY_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    ).install(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://runtime:8082",
    ) as client:
        denied = await client.get(
            "/internal/01980f66-b740-7dc8-9e1b-2714cf0c8801/assets/app.js?v=1"
        )
        response = await client.get(
            "/internal/01980f66-b740-7dc8-9e1b-2714cf0c8801/assets/app.js?v=1",
            headers={"X-Fairy-Runtime-Gateway-Key": GATEWAY_KEY},
        )

    assert denied.status_code == 404
    assert response.text == "ok"
    assert forwarded == ["http://127.0.0.1:43125/assets/app.js?v=1"]


@pytest.mark.asyncio
async def test_preview_proxy_rewrites_loopback_redirect_to_public_origin() -> None:
    async def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"location": "http://127.0.0.1:43125/login?next=%2F"},
        )

    app = FastAPI()
    CloudPreviewProxy(
        base_domain="preview.fairy.test",
        routes=FakeRoutes(),
        gateway_key=GATEWAY_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    ).install(app)
    token = "a" * 52
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"https://{token}.preview.fairy.test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/")

    assert response.headers["location"] == (f"https://{token}.preview.fairy.test/login?next=%2F")
