from __future__ import annotations

from uuid import UUID

import pytest

from fairy_core.assistant import application as application_module
from fairy_core.assistant.models import MessageRole
from fairy_core.providers import CancellationToken, ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


class _Boundary:
    def __init__(self, *, returns=False):
        self.result = None
        self.returns = returns
        self.model_round = 1
        self.usage = {}
        self.chunk_index = 0
        self.feedback = ()
        self.invalid_tool_retry_used = False

    def _yield(self, kind, payload):
        self.result = (kind, payload)
        if not self.returns:
            error_type = getattr(application_module, "AssistantModelYield", None)
            assert error_type is not None
            raise error_type

    def tools(self, **payload):
        self._yield("tools", payload)

    def draft(self, **payload):
        self._yield("draft", payload)

    def retry(self, **payload):
        self._yield("retry", payload)


def _round(kind):
    if kind == "text":
        delta = ModelDelta.text(profile_id="scripted", sequence=1, text="Hello from Fairy")
    elif kind == "direct":
        delta = ModelDelta.tool_call(
            profile_id="scripted", sequence=1, tool_call_id="direct", tool_name="direct_answer",
            arguments_fragment='{"answer":"Hello from Fairy"}',
        )
    else:
        delta = ModelDelta.tool_call(
            profile_id="scripted", sequence=1, tool_call_id="search", tool_name="web.search",
            arguments_fragment='{"query":"Python"}' if kind == "tools" else '{"query":',
        )
    return (delta, ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"))


@pytest.mark.parametrize("kind", ["text", "direct", "tools", "malformed"])
def test_model_boundary_yields_after_one_request_without_tools_or_finalization(tmp_path, kind):
    provider = ScriptedProvider([_round(kind), _round("text")])
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
    )
    boundary = _Boundary()
    try:
        task = _scratch_task(service, "Search public sources about Python")
        turn = _turn(service, task, f"model-boundary:{kind}")
        # Directly exercise the model layer while the Run remains paused in the Kernel.
        service._workflow_scheduler.close()
        app = service._assistant_application
        app.prepare_turn(UUID(turn["id"]), CancellationToken())
        # The boundary path owns node pause checks; this test removes only the initial Run pause.
        with service._unit_of_work_factory() as unit:
            unit.workflows.resume(UUID(turn["workflow_run_id"]))
            unit.commit()
        error_type = getattr(application_module, "AssistantModelYield", RuntimeError)
        with pytest.raises(error_type):
            app.run_turn(UUID(turn["id"]), CancellationToken(), boundary=boundary)
        assert len(provider.requests) == 1
        assert len(provider.rounds) == 1
        assert executor.calls == []
        assert boundary.result[0] == (
            "draft" if kind in {"text", "direct"} else "tools" if kind == "tools" else "retry"
        )
        with service._unit_of_work_factory() as unit:
            assert unit.assistant.message_for_turn(UUID(turn["id"]), MessageRole.ASSISTANT) is None
            assert unit.assistant.list_tool_invocations(UUID(turn["id"])) == ()
            assert unit.assistant.get_turn(UUID(turn["id"])).status == "running"
    finally:
        service.close()


def test_returning_boundary_cannot_fall_through_to_the_legacy_tool_loop(tmp_path):
    provider = ScriptedProvider([_round("tools"), _round("text")])
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Search public sources about Python")
        turn = _turn(service, task, "broken-boundary")
        service._workflow_scheduler.close()
        app = service._assistant_application
        app.prepare_turn(UUID(turn["id"]), CancellationToken())
        with service._unit_of_work_factory() as unit:
            unit.workflows.resume(UUID(turn["workflow_run_id"]))
            unit.commit()
        with pytest.raises(RuntimeError, match="boundary"):
            app.run_turn(UUID(turn["id"]), CancellationToken(), boundary=_Boundary(returns=True))
        assert len(provider.requests) == 1
        assert executor.calls == []
    finally:
        service.close()
