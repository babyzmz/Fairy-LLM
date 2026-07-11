from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import TextIO

from fairy_core.application.core import CoreApplication
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.service import CoreService
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.tools import ToolExecutor
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.persistence.data_directory_lock import DataDirectoryLock
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ProviderRegistry
from fairy_core.runtime.ports import RuntimeExecutor
from fairy_core.runtime.rust_worker import RustRuntimeExecutor
from fairy_core.runtime.unavailable import UnavailableRuntimeExecutor
from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from fairy_core.workspace.rust_worker import RustWorkspaceProvisioner
from fairy_core.workspace.worker_transport import SubprocessWorkerTransport


def build_local_service(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    runtime_executor: RuntimeExecutor | None = None,
    provider_registry: ProviderRegistry | None = None,
    tool_executor: ToolExecutor | None = None,
) -> CoreService:
    data_dir.mkdir(parents=True, exist_ok=True)
    resources = ExitStack()
    try:
        resources.callback(DataDirectoryLock.acquire(data_dir).close)
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
            resources.callback(transport.close)
            workspace_provisioner = RustWorkspaceProvisioner(transport, workspace_root)
            configured_runtime_executor: RuntimeExecutor = RustRuntimeExecutor(
                transport,
                managed_root=workspace_root,
            )
        else:
            workspace_provisioner = FileSystemWorkspaceProvisioner(workspace_root)
            configured_runtime_executor = UnavailableRuntimeExecutor(
                executor="rust_local_worker",
                diagnostic="Rust local worker is not configured",
            )
        selected_runtime_executor = runtime_executor or configured_runtime_executor
        registry = build_default_registry()
        engine = create_sqlite_core_engine(
            data_dir / "core.db",
            legacy_state_path=data_dir / "state.db",
            legacy_ledger_path=data_dir / "ledger.db",
        )
        resources.callback(engine.dispose)
        unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
        application = CoreApplication(
            unit_of_work_factory=unit_of_work_factory,
            workspace_provisioner=workspace_provisioner,
            registry=registry,
            policy=PolicyEngine(registry),
        )
        runtime_application = RuntimeApplication(
            unit_of_work_factory=unit_of_work_factory,
            executor=selected_runtime_executor,
            registry=registry,
            policy=PolicyEngine(registry),
            scope_resolver=application.scope_for_task,
        )
        AssistantLedgerApplication(
            unit_of_work_factory=unit_of_work_factory,
            scope_resolver=application.scope_for_task,
        ).recover_orphaned_turns()
        return CoreService(
            application,
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            provider_registry=provider_registry,
            tool_executor=tool_executor,
            runtime_application=runtime_application,
            on_close=resources.close,
        )
    except BaseException:
        resources.close()
        raise


def build_local_dispatcher(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    runtime_executor: RuntimeExecutor | None = None,
    provider_registry: ProviderRegistry | None = None,
    tool_executor: ToolExecutor | None = None,
) -> JsonRpcDispatcher:
    return JsonRpcDispatcher(
        build_local_service(
            data_dir,
            environment=environment,
            runtime_executor=runtime_executor,
            provider_registry=provider_registry,
            tool_executor=tool_executor,
        )
    )


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
