from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from fairy_core.assistant.ports import AssistantRepository
from fairy_core.assistant.repository import SqlAlchemyAssistantRepository
from fairy_core.commanding.ports import CommandLedger
from fairy_core.commanding.settings import ExecutionSettingsRepository
from fairy_core.commanding.settings_sqlalchemy import SqlAlchemyExecutionSettingsRepository
from fairy_core.commanding.sqlalchemy import SqlAlchemyCommandLedger
from fairy_core.documents.ports import DocumentRepository, DocumentSearchIndex
from fairy_core.documents.repository import SqlAlchemyDocumentRepository
from fairy_core.documents.search import SqlAlchemyDocumentSearchIndex
from fairy_core.knowledge.ports import KnowledgeRepository
from fairy_core.knowledge.repository import SqlAlchemyKnowledgeRepository
from fairy_core.mcp.repository import McpServerRepository, SqlAlchemyMcpServerRepository
from fairy_core.memory.ports import MemoryRepository
from fairy_core.memory.retrieval_ports import (
    MemoryProjectionWriter,
    MemorySearchIndex,
    MemorySnapshotRepository,
)
from fairy_core.memory.search_sqlalchemy import (
    SqlAlchemyMemoryProjectionWriter,
    SqlAlchemyMemorySearchIndex,
)
from fairy_core.memory.settings import MemorySettingsRepository
from fairy_core.memory.settings_sqlalchemy import SqlAlchemyMemorySettingsRepository
from fairy_core.memory.snapshot_sqlalchemy import SqlAlchemyMemorySnapshotRepository
from fairy_core.memory.sqlalchemy import SqlAlchemyMemoryRepository
from fairy_core.model_catalog.ports import ModelCatalogRepository
from fairy_core.model_catalog.repository import SqlAlchemyModelCatalogRepository
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.presentation.ports import PresentationRepository
from fairy_core.presentation.repository import SqlAlchemyPresentationRepository
from fairy_core.realtime.ports import RealtimeRepository
from fairy_core.realtime.repository import SqlAlchemyRealtimeRepository
from fairy_core.storage.ports import StateStore
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from fairy_core.workspace.ports import ProjectIndexRepository, WorkspaceRepository
from fairy_core.workspace.repository import (
    SqlAlchemyProjectIndexRepository,
    SqlAlchemyWorkspaceRepository,
)


class CoreUnitOfWork(Protocol):
    state: StateStore
    assistant: AssistantRepository
    commands: CommandLedger
    execution_settings: ExecutionSettingsRepository
    mcp_servers: McpServerRepository
    memory: MemoryRepository
    memory_settings: MemorySettingsRepository
    snapshots: MemorySnapshotRepository
    memory_search: MemorySearchIndex
    memory_projections: MemoryProjectionWriter
    documents: DocumentRepository
    document_search: DocumentSearchIndex
    knowledge: KnowledgeRepository
    workspaces: WorkspaceRepository
    project_indexes: ProjectIndexRepository
    presentations: PresentationRepository
    model_catalog: ModelCatalogRepository
    realtime: RealtimeRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class CoreUnitOfWorkFactory(Protocol):
    def __call__(self) -> CoreUnitOfWork: ...


class SqlAlchemyUnitOfWork:
    def __init__(self, engine: Engine, *, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self._committed = False

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("unit of work is already active")
        connection = self._engine.connect()
        transaction: Transaction | None = None
        try:
            transaction = connection.begin()
            self._committed = False
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": self._tenant_id},
                )
            self.state = SqlAlchemyStateStore(connection, tenant_id=self._tenant_id)
            self.assistant = SqlAlchemyAssistantRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.commands = SqlAlchemyCommandLedger(connection, tenant_id=self._tenant_id)
            self.execution_settings = SqlAlchemyExecutionSettingsRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.mcp_servers = SqlAlchemyMcpServerRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.memory = SqlAlchemyMemoryRepository(connection, tenant_id=self._tenant_id)
            self.memory_settings = SqlAlchemyMemorySettingsRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.snapshots = SqlAlchemyMemorySnapshotRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.memory_search = SqlAlchemyMemorySearchIndex(
                connection,
                tenant_id=self._tenant_id,
            )
            self.memory_projections = SqlAlchemyMemoryProjectionWriter(
                connection,
                tenant_id=self._tenant_id,
            )
            self.documents = SqlAlchemyDocumentRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.document_search = SqlAlchemyDocumentSearchIndex(
                connection,
                tenant_id=self._tenant_id,
            )
            self.knowledge = SqlAlchemyKnowledgeRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.workspaces = SqlAlchemyWorkspaceRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.project_indexes = SqlAlchemyProjectIndexRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.presentations = SqlAlchemyPresentationRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.model_catalog = SqlAlchemyModelCatalogRepository(
                connection,
                tenant_id=self._tenant_id,
            )
            self.realtime = SqlAlchemyRealtimeRepository(
                connection,
                tenant_id=self._tenant_id,
            )
        except BaseException:
            try:
                if transaction is not None and transaction.is_active:
                    transaction.rollback()
            finally:
                connection.close()
            raise
        self._connection = connection
        self._transaction = transaction
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        transaction = self._transaction
        connection = self._connection
        try:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
        finally:
            if connection is not None:
                connection.close()
            self._connection = None
            self._transaction = None

    def commit(self) -> None:
        if self._transaction is None or not self._transaction.is_active:
            raise RuntimeError("unit of work is not active")
        if self._committed:
            raise RuntimeError("unit of work has already committed")
        self._transaction.commit()
        self._committed = True


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, engine: Engine, *, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = normalize_tenant_id(tenant_id)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._engine, tenant_id=self._tenant_id)
