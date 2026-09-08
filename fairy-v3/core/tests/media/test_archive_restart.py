from time import monotonic, sleep
from uuid import UUID

import pytest

from fairy_core.media.workflow import MediaGenerationWorkflowAdapter
from fairy_core.providers import ProviderCancelledError
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.scheduler import WorkflowPaused
from tests.media.test_media_service import RecordingMediaProvider, _scratch_task


def test_restart_finishes_both_archives_without_submitting_completed_jobs_again(
    tmp_path, monkeypatch,
):
    original = MediaGenerationWorkflowAdapter._archive

    def interrupted_archive(self, job_id):
        with self._unit_of_work_factory() as unit:
            workflow = unit.workflows.get_by_owner(
                owner_kind="media_generation", owner_id=str(job_id), engine_version=1,
            )
            unit.workflows.request_pause(workflow.run.id)
            unit.commit()
        raise WorkflowPaused

    monkeypatch.setattr(MediaGenerationWorkflowAdapter, "_archive", interrupted_archive)
    provider = RecordingMediaProvider()
    first = build_local_service(tmp_path, media_provider=provider)
    bindings = []
    try:
        for index in range(2):
            task = _scratch_task(first, f"Generate scoped image {index}")
            with pytest.raises(ProviderCancelledError):
                first.invoke("media.images.generate", {
                    "task_id": task["id"], "prompt": f"Image {index}",
                    "idempotency_key": f"archive-restart:{index}",
                })
            with first._unit_of_work_factory() as unit:
                (job,) = unit.state.list_media_jobs(UUID(task["id"]))
                workflow = unit.workflows.get_by_owner(
                    owner_kind="media_generation", owner_id=str(job.id), engine_version=1,
                )
                assert job.status == "completed"
                assert workflow.run.status == "paused"
                assert [node.kind for node in workflow.nodes if node.status != "succeeded"] == [
                    "media.generation.archive",
                ]
                bindings.append((task["id"], job.id, workflow.run.id, job.artifact_id))
        with first._unit_of_work_factory() as unit:
            assert unit.state.recoverable_media_jobs() == []
            assert {job.id for job in unit.state.recoverable_media_jobs(
                include_unsettled_workflows=True,
            )} == {item[1] for item in bindings}
    finally:
        first.close()
    assert len(provider.image_requests) == 2
    monkeypatch.setattr(MediaGenerationWorkflowAdapter, "_archive", original)
    recovered_provider = RecordingMediaProvider()
    second = build_local_service(tmp_path, media_provider=recovered_provider)
    try:
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with second._unit_of_work_factory() as unit:
                states = [unit.workflows.get_run(binding[2]).status for binding in bindings]
            if states == ["completed", "completed"]:
                break
            sleep(0.02)
        assert states == ["completed", "completed"], "Completed Jobs still need archive recovery"
        for task_id, job_id, run_id, artifact_id in bindings:
            with second._unit_of_work_factory() as unit:
                (job,) = unit.state.list_media_jobs(UUID(task_id))
                assert job.id == job_id
                assert job.artifact_id == artifact_id
                assert unit.state.get_artifact(artifact_id).task_id == UUID(task_id)
                assert unit.workflows.get_run(run_id).owner_id == str(job_id)
        assert recovered_provider.image_requests == []
        with second._unit_of_work_factory() as unit:
            assert unit.state.recoverable_media_jobs(include_unsettled_workflows=True) == []
    finally:
        second.close()
