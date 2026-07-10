from __future__ import annotations

import atexit
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from fairy_core.application.core import CoreApplication
from fairy_core.commanding.bus import CommandBus
from fairy_core.commanding.ledger import SqliteCommandLedger
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.storage import SqliteStateStore
from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from fairy_core.workspace.rust_worker import RustWorkspaceProvisioner
from fairy_core.workspace.worker_transport import SubprocessWorkerTransport


def build_local_dispatcher(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> JsonRpcDispatcher:
    data_dir.mkdir(parents=True, exist_ok=True)
    configured = dict(os.environ if environment is None else environment)
    workspace_root = data_dir / "workspaces"
    worker_program = configured.get("FAIRY_LOCAL_WORKER_PROGRAM", "").strip()
    if worker_program:
        raw_args = configured.get("FAIRY_LOCAL_WORKER_ARGS_JSON", "[]")
        parsed_args = json.loads(raw_args)
        if not isinstance(parsed_args, list) or not all(
            isinstance(argument, str) for argument in parsed_args
        ):
            raise ValueError("FAIRY_LOCAL_WORKER_ARGS_JSON must be a JSON string array")
        transport = SubprocessWorkerTransport(
            program=worker_program,
            args=tuple(parsed_args),
            environment={"FAIRY_MANAGED_ROOT": str(workspace_root)},
        )
        atexit.register(transport.close)
        workspace_provisioner = RustWorkspaceProvisioner(transport, workspace_root)
    else:
        workspace_provisioner = FileSystemWorkspaceProvisioner(workspace_root)
    registry = build_default_registry()
    ledger = SqliteCommandLedger(data_dir / "ledger.db")
    application = CoreApplication(
        state_store=SqliteStateStore(data_dir / "state.db"),
        workspace_provisioner=workspace_provisioner,
        command_bus=CommandBus(
            registry=registry,
            policy=PolicyEngine(registry),
            ledger=ledger,
        ),
    )
    return JsonRpcDispatcher(application, ledger=ledger, registry=registry)


def process_stream(
    dispatcher: JsonRpcDispatcher,
    source: TextIO,
    destination: TextIO,
) -> None:
    for line in source:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = dispatcher.dispatch(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                    "data": {"details": str(exc)},
                },
            }
        destination.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        destination.write("\n")
        destination.flush()


def main() -> None:
    configured = os.environ.get("FAIRY_V3_DATA_DIR", "").strip()
    data_dir = Path(configured) if configured else Path.home() / ".fairy-v3"
    process_stream(build_local_dispatcher(data_dir), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
