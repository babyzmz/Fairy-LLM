"""Compatibility entry point for the renamed Outbox Worker."""

from fairy_cloud.workers.outbox import (
    OutboxHandler,
    OutboxStore,
    OutboxWorker,
    WorkerCycle,
    main,
)

__all__ = [
    "OutboxHandler",
    "OutboxStore",
    "OutboxWorker",
    "WorkerCycle",
    "main",
]


if __name__ == "__main__":
    main()
