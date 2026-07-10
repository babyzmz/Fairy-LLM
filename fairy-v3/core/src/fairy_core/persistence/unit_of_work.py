from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from fairy_core.commanding.ports import CommandLedger
from fairy_core.commanding.sqlalchemy import SqlAlchemyCommandLedger
from fairy_core.memory.ports import MemoryRepository
from fairy_core.memory.retrieval_ports import MemorySnapshotRepository
from fairy_core.memory.snapshot_sqlalchemy import SqlAlchemyMemorySnapshotRepository
from fairy_core.memory.sqlalchemy import SqlAlchemyMemoryRepository
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.ports import StateStore
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore


class CoreUnitOfWork(Protocol):
    state: StateStore
    commands: CommandLedger
    memory: MemoryRepository
    snapshots: MemorySnapshotRepository

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
            self.commands = SqlAlchemyCommandLedger(connection, tenant_id=self._tenant_id)
            self.memory = SqlAlchemyMemoryRepository(connection, tenant_id=self._tenant_id)
            self.snapshots = SqlAlchemyMemorySnapshotRepository(
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
