from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fairy_core.commanding import CommandStatus
from fairy_core.commanding.registry import RiskLevel, build_default_registry
from fairy_core.domain.errors import WorkerFenceError
from fairy_core.media.workflow import MediaGenerationWorkflowAdapter
from tests.test_command_bus import _scope
from tests.workflow.test_repository import _factory


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("same_owner", [True, False])
def test_late_media_settlement_cannot_borrow_current_command_fence(
    tmp_path, success, same_owner,
):
    path = tmp_path / "core.db"
    factory = _factory(path)
    with factory() as unit:
        commands = []
        for index in range(2):
            run = unit.commands.create_run(
                command_name="media.images.generate", actor="user",
                scope=_scope(tmp_path / str(index)), input_payload={},
                risk_level=RiskLevel.HIGH, idempotency_key=f"media:{index}",
            )
            unit.commands.transition(run.id, CommandStatus.QUEUED)
            commands.append(unit.commands.claim(
                run.id, worker_id="old-worker",
                lease_until=datetime.now(UTC) + timedelta(minutes=2),
            ))
        stale, other = commands
        assert unit.commands.abandon(
            stale.id, lease_owner=stale.lease_owner, lease_fence=stale.lease_fence,
        )
        current = unit.commands.claim(
            stale.id, worker_id="old-worker" if same_owner else "new-worker",
            lease_until=datetime.now(UTC) + timedelta(minutes=2),
        )
        unit.commit()
    adapter = MediaGenerationWorkflowAdapter(
        application=None, unit_of_work_factory=factory, registry=build_default_registry(),
    )
    result = SimpleNamespace(
        job=SimpleNamespace(id=stale.id, status=SimpleNamespace(value="completed")),
        artifact=None,
    )
    with pytest.raises(WorkerFenceError):
        adapter._settle_command(
            stale, result=result if success else None,
            error=None if success else RuntimeError("late provider failure"),
        )
    with _factory(path)() as unit:
        assert unit.commands.get_run(current.id) == current
        assert unit.commands.get_run(other.id) == other
