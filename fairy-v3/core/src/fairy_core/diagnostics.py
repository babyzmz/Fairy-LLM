"""Bounded counters only: no prompts, SQL text, parameters, paths or credentials."""

from threading import Lock
from time import monotonic_ns

from sqlalchemy import event


class RuntimeCounters:
    def __init__(self):
        self._lock = Lock()
        self._values = {}

    def observe(self, name, elapsed_ns=0):
        if name not in {
            "sql",
            "rpc.control.queue",
            "rpc.read.queue",
            "rpc.serial.queue",
            "rpc.control.execute",
            "rpc.read.execute",
            "rpc.serial.execute",
            "rpc.rejected",
        }:
            raise ValueError("Diagnostic label is not allowlisted")
        with self._lock:
            value = self._values.setdefault(name, [0, 0, 0])
            value[0] += 1
            elapsed = max(0, int(elapsed_ns))
            value[1] += elapsed
            value[2] = max(value[2], elapsed)

    def snapshot(self):
        with self._lock:
            return {
                name: {
                    "count": value[0],
                    "total_ms": value[1] / 1_000_000,
                    "max_ms": value[2] / 1_000_000,
                }
                for name, value in self._values.items()
            }


def attach_sql_counters(engine):
    counters = RuntimeCounters()
    engine.fairy_diagnostics = counters

    @event.listens_for(engine, "before_cursor_execute")
    def before(_connection, _cursor, _statement, _parameters, context, _many):
        context._fairy_started_ns = monotonic_ns()

    @event.listens_for(engine, "after_cursor_execute")
    def after(_connection, _cursor, _statement, _parameters, context, _many):
        counters.observe("sql", monotonic_ns() - context._fairy_started_ns)


def local_runtime_snapshot(service):
    factory = service._unit_of_work_factory
    engine = getattr(factory, "_engine", None)
    counters = getattr(engine, "fairy_diagnostics", None)
    return {
        "schema_version": 1,
        "database": counters.snapshot() if counters is not None else {},
        "workflow": service._workflow_scheduler.diagnostics(),
        "browser": (
            service._browser_service.diagnostics()
            if service._browser_service is not None
            else {"available": False}
        ),
        "scope": "local_host_counters_no_content",
    }
