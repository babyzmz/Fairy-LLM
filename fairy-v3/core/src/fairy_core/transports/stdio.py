from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import TextIO

from fairy_core.application.core import CoreApplication
from fairy_core.application.runtime import RuntimeApplication
from fairy_core.application.service import CoreService
from fairy_core.assistant.tools import ToolExecutor
from fairy_core.browser import BrowserService
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.commanding.settings import ExecutionPolicyResolver, SandboxHealthProvider
from fairy_core.documents.ports import DocumentBlobStore, DocumentParser
from fairy_core.mcp.application import McpApplication
from fairy_core.mcp.ports import McpConnector
from fairy_core.mcp.sdk import MappingCredentialResolver, OfficialMcpConnector
from fairy_core.media.ports import MediaProvider
from fairy_core.media.staging import MediaStagingStore
from fairy_core.model_catalog.ports import ModelCatalogSource
from fairy_core.obsidian import ObsidianConnector
from fairy_core.perception import ImageAttachmentStore
from fairy_core.persistence.data_directory_lock import DataDirectoryLock
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ProviderRegistry
from fairy_core.research.ports import FetchPort
from fairy_core.runtime.evidence_store import FileRuntimeEvidenceStore
from fairy_core.runtime.http_review import HttpRuntimeReviewer
from fairy_core.runtime.ports import RuntimeExecutor
from fairy_core.runtime.rust_worker import RustRuntimeExecutor
from fairy_core.runtime.supervisor import RoutedRuntimeExecutor, WslDynamicRuntimeExecutor
from fairy_core.runtime.unavailable import UnavailableRuntimeExecutor
from fairy_core.sandbox.ports import SandboxExecutor
from fairy_core.sandbox.tools import ExecutorSandboxHealthProvider
from fairy_core.sandbox.wsl import WslSandboxExecutor
from fairy_core.skills.loader import SkillPackageLoader
from fairy_core.skills.manager import SkillManager
from fairy_core.skills.registry import SkillRegistry
from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio_dispatch import StdioRequestDispatcher
from fairy_core.voice import VoiceRegistry
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from fairy_core.workspace.rust_worker import RustWorkspaceProvisioner
from fairy_core.workspace.worker_transport import (
    RestartingWorkerTransport,
    RustSystemActionWorker,
    SubprocessWorkerTransport,
)


def build_local_service(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    runtime_executor: RuntimeExecutor | None = None,
    provider_registry: ProviderRegistry | None = None,
    voice_registry: VoiceRegistry | None = None,
    image_attachment_store: ImageAttachmentStore | None = None,
    tool_executor: ToolExecutor | None = None,
    research_fetch_port: FetchPort | None = None,
    document_parser: DocumentParser | None = None,
    document_blob_store: DocumentBlobStore | None = None,
    sandbox_executor: SandboxExecutor | None = None,
    sandbox_health_provider: SandboxHealthProvider | None = None,
    mcp_connector: McpConnector | None = None,
    skill_paths: Iterable[Path] | None = None,
    model_catalog_source: ModelCatalogSource | None = None,
    media_provider: MediaProvider | None = None,
    media_staging_store: MediaStagingStore | None = None,
    assistant_workflow_engine_version: int = 3,
) -> CoreService:
    data_dir.mkdir(parents=True, exist_ok=True)
    resources = ExitStack()
    try:
        resources.callback(DataDirectoryLock.acquire(data_dir).close)
        configured = dict(os.environ if environment is None else environment)
        workspace_root = data_dir / "workspaces"
        worker_program = configured.get("FAIRY_LOCAL_WORKER_PROGRAM", "").strip()
        system_action_worker = None
        if worker_program:
            raw_args = configured.get("FAIRY_LOCAL_WORKER_ARGS_JSON", "[]")
            parsed_args = json.loads(raw_args)
            if not isinstance(parsed_args, list) or not all(
                isinstance(argument, str) for argument in parsed_args
            ):
                raise ValueError("FAIRY_LOCAL_WORKER_ARGS_JSON must be a JSON string array")
            worker_environment = {"FAIRY_MANAGED_ROOT": str(workspace_root)}
            configured_git = configured.get("FAIRY_GIT_PROGRAM", "").strip()
            if configured_git:
                worker_environment["FAIRY_GIT_PROGRAM"] = configured_git
            transport = SubprocessWorkerTransport(
                program=worker_program,
                args=tuple(parsed_args),
                environment=worker_environment,
            )
            resources.callback(transport.close)
            system_action_worker = RustSystemActionWorker(transport)
            workspace_provisioner = RustWorkspaceProvisioner(transport, workspace_root)
            configured_runtime_executor: RuntimeExecutor = RustRuntimeExecutor(
                transport,
                managed_root=workspace_root,
            )
        else:
            workspace_provisioner = FileSystemWorkspaceProvisioner(workspace_root)
            resources.callback(workspace_provisioner.close)
            configured_runtime_executor = UnavailableRuntimeExecutor(
                executor="rust_local_worker",
                diagnostic="Rust local worker is not configured",
            )
        selected_runtime_executor = runtime_executor or RoutedRuntimeExecutor(
            static=configured_runtime_executor,
            local_dynamic=WslDynamicRuntimeExecutor(host_environment=configured),
        )
        browser_program = configured.get("FAIRY_BROWSER_REVIEW_PROGRAM", "").strip()
        if not browser_program:
            edge = (
                Path(configured.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
                / "Microsoft"
                / "Edge"
                / "Application"
                / "msedge.exe"
            )
            browser_program = str(edge) if edge.is_file() else ""
        registry = build_default_registry()
        skills = SkillRegistry(registry)
        configured_skill_paths = tuple(skill_paths or ())
        default_skills_root = data_dir / "skills"
        skill_manager = SkillManager(default_skills_root, skills)
        loader = SkillPackageLoader()
        if configured_skill_paths:
            for skill_path in configured_skill_paths:
                skills.install(loader.load(skill_path))
        else:
            skill_manager.load_installed()
        engine = create_sqlite_core_engine(
            data_dir / "core.db",
            legacy_state_path=data_dir / "state.db",
            legacy_ledger_path=data_dir / "ledger.db",
        )
        resources.callback(engine.dispose)
        unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
        selected_sandbox_executor = sandbox_executor or WslSandboxExecutor(
            host_environment=configured,
        )
        selected_sandbox_health = sandbox_health_provider or ExecutorSandboxHealthProvider(
            selected_sandbox_executor,
            execution_target="local",
        )
        execution_policy = ExecutionPolicyResolver(selected_sandbox_health)
        application = CoreApplication(
            unit_of_work_factory=unit_of_work_factory,
            workspace_provisioner=workspace_provisioner,
            registry=registry,
            policy=PolicyEngine(registry),
            execution_policy=execution_policy,
        )
        raw_mcp_credentials = json.loads(configured.get("FAIRY_MCP_CREDENTIALS_JSON", "{}"))
        if not isinstance(raw_mcp_credentials, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_mcp_credentials.items()
        ):
            raise ValueError("FAIRY_MCP_CREDENTIALS_JSON must be a JSON string map")
        inherited_process_environment = {
            name: configured[name]
            for name in (
                "LOCALAPPDATA",
                "PATH",
                "PATHEXT",
                "SYSTEMDRIVE",
                "SYSTEMROOT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "WINDIR",
            )
            if configured.get(name)
        }
        selected_mcp_connector = mcp_connector or OfficialMcpConnector(
            credentials=MappingCredentialResolver(raw_mcp_credentials),
            base_environment=inherited_process_environment,
        )
        mcp_application = McpApplication(
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            connector=selected_mcp_connector,
            scope_resolver=application.scope_for_task,
            execution_policy=execution_policy,
            execution_target="local",
        )
        runtime_application = RuntimeApplication(
            unit_of_work_factory=unit_of_work_factory,
            executor=selected_runtime_executor,
            registry=registry,
            policy=PolicyEngine(registry),
            scope_resolver=application.scope_for_task,
            execution_policy=execution_policy,
        )
        browser_worker_program = configured.get("FAIRY_BROWSER_WORKER_PROGRAM", "").strip()
        browser_worker_args: tuple[str, ...] = ()
        if not browser_worker_program:
            node_program = shutil.which("node", path=configured.get("PATH"))
            development_worker = (
                Path(__file__).resolve().parents[4] / "desktop" / "browser-worker.mjs"
            )
            if node_program and development_worker.is_file():
                browser_worker_program = node_program
                browser_worker_args = (str(development_worker),)
        else:
            raw_browser_args = json.loads(configured.get("FAIRY_BROWSER_WORKER_ARGS_JSON", "[]"))
            if not isinstance(raw_browser_args, list) or not all(
                isinstance(argument, str) for argument in raw_browser_args
            ):
                raise ValueError("FAIRY_BROWSER_WORKER_ARGS_JSON must be a JSON string array")
            browser_worker_args = tuple(raw_browser_args)
        browser_transport = None
        if browser_worker_program:
            browser_transport = RestartingWorkerTransport(
                lambda: SubprocessWorkerTransport(
                    program=browser_worker_program,
                    args=browser_worker_args,
                    request_timeout_seconds=120,
                    environment={"FAIRY_BROWSER_DATA_DIR": str(data_dir / "browser")},
                    current_directory=(
                        Path(browser_worker_args[0]).parent if browser_worker_args else None
                    ),
                )
            )
            resources.callback(browser_transport.close)
        browser_service = BrowserService(
            worker=browser_transport,
            state_path=data_dir / "browser" / "sessions.json",
            profile_root=data_dir / "browser" / "profiles",
        )
        service = CoreService(
            application,
            assistant_workflow_engine_version=assistant_workflow_engine_version,
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            provider_registry=provider_registry,
            voice_registry=voice_registry,
            image_attachment_store=(
                image_attachment_store
                if image_attachment_store is not None
                else ImageAttachmentStore(data_dir / "perception")
            ),
            tool_executor=tool_executor,
            research_fetch_port=research_fetch_port,
            document_parser=document_parser,
            document_blob_store=document_blob_store,
            runtime_application=runtime_application,
            runtime_reviewer=HttpRuntimeReviewer(
                browser_executable=(Path(browser_program) if browser_program else None),
                browser_scratch_root=data_dir / "runtime-browser",
                host_environment=configured,
            ),
            runtime_evidence_store=FileRuntimeEvidenceStore(data_dir / "runtime-evidence"),
            system_action_worker=system_action_worker,
            sandbox_executor=selected_sandbox_executor,
            sandbox_health_provider=selected_sandbox_health,
            skill_registry=skills,
            skill_manager=skill_manager,
            mcp_application=mcp_application,
            model_catalog_source=model_catalog_source,
            media_provider=media_provider,
            obsidian_connector=ObsidianConnector(
                configured,
                registry_path=data_dir / "obsidian-sources.json",
                path_registry_path=data_dir / "obsidian-paths.json",
            ),
            browser_service=browser_service,
            media_staging_store=(
                media_staging_store
                if media_staging_store is not None
                else MediaStagingStore(data_dir / "media-staging")
            )
            if media_provider is not None
            else None,
            workspace_provisioner=(workspace_provisioner if media_provider is not None else None),
            default_execution_target="local",
            on_close=resources.close,
        )
        # DataDirectoryLock above proves the previous local stream owner has exited.
        service._assistant_ledger.recover_local_cancelled_model_streams()
        service.recover_interrupted_work(verify_running_previews=True)
        return service
    except BaseException:
        resources.close()
        raise


def build_local_dispatcher(
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    runtime_executor: RuntimeExecutor | None = None,
    provider_registry: ProviderRegistry | None = None,
    voice_registry: VoiceRegistry | None = None,
    image_attachment_store: ImageAttachmentStore | None = None,
    tool_executor: ToolExecutor | None = None,
    research_fetch_port: FetchPort | None = None,
    document_parser: DocumentParser | None = None,
    document_blob_store: DocumentBlobStore | None = None,
    sandbox_executor: SandboxExecutor | None = None,
    sandbox_health_provider: SandboxHealthProvider | None = None,
    mcp_connector: McpConnector | None = None,
    skill_paths: Iterable[Path] | None = None,
    model_catalog_source: ModelCatalogSource | None = None,
    media_provider: MediaProvider | None = None,
    media_staging_store: MediaStagingStore | None = None,
) -> JsonRpcDispatcher:
    return JsonRpcDispatcher(
        build_local_service(
            data_dir,
            environment=environment,
            runtime_executor=runtime_executor,
            provider_registry=provider_registry,
            voice_registry=voice_registry,
            image_attachment_store=image_attachment_store,
            tool_executor=tool_executor,
            research_fetch_port=research_fetch_port,
            document_parser=document_parser,
            document_blob_store=document_blob_store,
            sandbox_executor=sandbox_executor,
            sandbox_health_provider=sandbox_health_provider,
            mcp_connector=mcp_connector,
            skill_paths=skill_paths,
            model_catalog_source=model_catalog_source,
            media_provider=media_provider,
            media_staging_store=media_staging_store,
        )
    )


def process_stream(
    dispatcher: JsonRpcDispatcher,
    source: TextIO,
    destination: TextIO,
) -> None:
    transport = StdioRequestDispatcher(dispatcher, destination)
    try:
        for line in source:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
            except (json.JSONDecodeError, ValueError) as exc:
                transport.write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error",
                            "data": {"details": str(exc)},
                        },
                    }
                )
            else:
                transport.dispatch(request)
    finally:
        transport.close()


def main() -> None:
    configured = os.environ.get("FAIRY_V3_DATA_DIR", "").strip()
    data_dir = Path(configured) if configured else Path.home() / ".fairy-v3"
    dispatcher = build_local_dispatcher(data_dir)
    try:
        process_stream(dispatcher, sys.stdin, sys.stdout)
    finally:
        dispatcher.close()


if __name__ == "__main__":
    main()
