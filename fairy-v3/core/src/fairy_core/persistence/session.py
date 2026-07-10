from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Connection, Engine


class SqlAlchemySession:
    """Provides repository read/write scopes over an Engine or caller transaction."""

    def __init__(
        self,
        bind: Engine | Connection,
        *,
        owns_engine: bool = False,
    ) -> None:
        if owns_engine and isinstance(bind, Connection):
            raise ValueError("a connection-bound session cannot own its engine")
        self._bind = bind
        self._owns_engine = owns_engine

    @property
    def bind(self) -> Engine | Connection:
        return self._bind

    @property
    def dialect_name(self) -> str:
        return self._bind.dialect.name

    @contextmanager
    def read(self) -> Iterator[Connection]:
        if isinstance(self._bind, Connection):
            yield self._bind
            return
        with self._bind.connect() as connection:
            yield connection

    @contextmanager
    def write(self) -> Iterator[Connection]:
        if isinstance(self._bind, Connection):
            yield self._bind
            return
        with self._bind.begin() as connection:
            yield connection

    def close(self) -> None:
        if self._owns_engine:
            assert isinstance(self._bind, Engine)
            self._bind.dispose()
