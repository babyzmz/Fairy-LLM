from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio import build_local_service, process_stream

from fairy_capabilities.composition import build_provider_registry, build_tool_executor


def build_composed_local_dispatcher(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> JsonRpcDispatcher:
    configured = dict(os.environ if environment is None else environment)
    return JsonRpcDispatcher(
        build_local_service(
            data_dir,
            environment=configured,
            provider_registry=build_provider_registry(configured),
            tool_executor=build_tool_executor(),
        )
    )


def main() -> None:
    configured = os.environ.get("FAIRY_V3_DATA_DIR", "").strip()
    data_dir = Path(configured) if configured else Path.home() / ".fairy-v3"
    process_stream(build_composed_local_dispatcher(data_dir), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
