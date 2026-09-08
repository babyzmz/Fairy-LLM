from types import SimpleNamespace
from uuid import uuid4

from fairy_core.media.models import MediaGenerationStatus
from fairy_core.media.tools import _tool_result


def test_active_video_receipt_does_not_require_or_invent_an_artifact():
    job_id = uuid4()
    receipt = _tool_result(SimpleNamespace(
        artifact=None,
        job=SimpleNamespace(id=job_id, progress=5, status=MediaGenerationStatus.PENDING),
    ))
    assert receipt.artifact_ids == ()
    assert str(job_id) in receipt.model_content
    assert "pending" in receipt.model_content
    assert "5%" in receipt.public_summary
