from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import boto3
import uvicorn
from botocore.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import OidcTokenVerifier, RemoteJwksProvider
from fairy_cloud.dispatchers import TenantDispatcherRegistry
from fairy_cloud.settings import CloudSettings
from fairy_cloud.storage.objects import S3ObjectStore
from fairy_cloud.storage.postgres import PostgresSyncStore

settings = CloudSettings()
engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
sync_store = PostgresSyncStore(engine)
jwks = RemoteJwksProvider(
    issuer=settings.oidc_issuer,
    allow_insecure_http=settings.oidc_allow_insecure_http,
)
authenticator = OidcTokenVerifier(
    issuer=settings.oidc_issuer,
    audience=settings.oidc_audience,
    key_provider=jwks,
    required_scopes=frozenset({"fairy.api"}),
)
s3_client = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
    region_name=settings.s3_region,
    config=Config(
        signature_version="s3v4",
        retries={"mode": "standard", "max_attempts": 3},
        s3={"addressing_style": "path"},
    ),
)
object_store = S3ObjectStore(client=s3_client, bucket=settings.s3_bucket)
dispatchers = TenantDispatcherRegistry(root=settings.core_data_dir)
dispatcher = dispatchers.system_dispatcher()


async def readiness() -> dict[str, str]:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    await asyncio.to_thread(s3_client.head_bucket, Bucket=settings.s3_bucket)
    return {"status": "ready", "postgres": "ok", "object_store": "ok"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await jwks.close()
    await engine.dispose()


app = create_cloud_app(
    dispatcher,
    authenticator=authenticator,
    sync_store=sync_store,
    object_store=object_store,
    readiness=readiness,
    lifespan=lifespan,
    dispatcher_resolver=dispatchers.for_identity,
)


def run() -> None:
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    run()
