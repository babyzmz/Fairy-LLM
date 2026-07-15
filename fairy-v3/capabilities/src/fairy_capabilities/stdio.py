from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio import build_local_service, process_stream

from fairy_capabilities.composition import (
    build_capability_bundle,
    build_local_sandbox,
    build_model_catalog_source,
    build_provider_registry,
    build_voice_registry,
)
from fairy_capabilities.documents import CompositeDocumentParser, ManagedFileDocumentStore


def build_composed_local_dispatcher(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> JsonRpcDispatcher:
    configured = dict(os.environ if environment is None else environment)
    providers = build_provider_registry(configured)
    try:
        model_catalog = build_model_catalog_source(configured)
    except BaseException:
        providers.close()
        raise
    try:
        voice = build_voice_registry(configured)
    except BaseException:
        model_catalog.close()
        providers.close()
        raise
    try:
        capabilities = build_capability_bundle(configured)
    except BaseException:
        voice.close()
        model_catalog.close()
        providers.close()
        raise
    sandbox = build_local_sandbox(configured)
    try:
        service = build_local_service(
            data_dir,
            environment=configured,
            provider_registry=providers,
            voice_registry=voice,
            tool_executor=capabilities.executor,
            research_fetch_port=capabilities.web.fetch_port,
            document_parser=CompositeDocumentParser(),
            document_blob_store=ManagedFileDocumentStore(data_dir / "documents"),
            sandbox_executor=sandbox.executor,
            sandbox_health_provider=sandbox.health,
            model_catalog_source=model_catalog,
        )
    except BaseException:
        capabilities.executor.close()
        voice.close()
        model_catalog.close()
        providers.close()
        raise
    return JsonRpcDispatcher(service)


def main() -> None:
    configured = os.environ.get("FAIRY_V3_DATA_DIR", "").strip()
    data_dir = Path(configured) if configured else Path.home() / ".fairy-v3"
    process_stream(build_composed_local_dispatcher(data_dir), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
