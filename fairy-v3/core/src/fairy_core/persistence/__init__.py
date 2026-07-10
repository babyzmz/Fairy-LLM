from typing import Any

from fairy_core.persistence.session import SqlAlchemySession

__all__ = [
    "CoreUnitOfWork",
    "CoreUnitOfWorkFactory",
    "SqlAlchemySession",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyUnitOfWorkFactory",
    "create_sqlite_core_engine",
]


def __getattr__(name: str) -> Any:
    if name not in {
        "CoreUnitOfWork",
        "CoreUnitOfWorkFactory",
        "SqlAlchemyUnitOfWork",
        "SqlAlchemyUnitOfWorkFactory",
        "create_sqlite_core_engine",
    }:
        raise AttributeError(name)
    if name == "create_sqlite_core_engine":
        from fairy_core.persistence.sqlite import create_sqlite_core_engine

        return create_sqlite_core_engine
    from fairy_core.persistence import unit_of_work

    return getattr(unit_of_work, name)
