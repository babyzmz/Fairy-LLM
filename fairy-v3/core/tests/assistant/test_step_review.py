from functools import partial

import pytest

from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from fairy_core.workflow.scheduler import WorkflowRetryableError
from tests.assistant import test_model_routing as contracts


@pytest.mark.parametrize("lost_receipt", [False, True])
def test_review_is_a_durable_node_and_preserves_persona_projection_contract(
    tmp_path,
    monkeypatch,
    lost_receipt,
):
    monkeypatch.setattr(
        contracts,
        "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    original = SqlAlchemyWorkflowRepository.complete
    reviews = []
    lost = []

    def complete(repository, claim, **kwargs):
        snapshot = repository.get(claim.run_id)
        node = next(item for item in snapshot.nodes if item.id == claim.node_id)
        if node.kind == "assistant.step.review":
            reviews.append(node.id)
            assert node.result is not None
            assert node.result["content"] == "Reviewed final answer."
            assert node.result["reviewed"] is True
            if lost_receipt and not lost:
                lost.append(node.id)
                raise WorkflowRetryableError("Review receipt lost")
        return original(repository, claim, **kwargs)

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "complete", complete)
    contracts.test_multi_model_review_streams_only_the_single_final_answer(tmp_path)
    assert len(set(reviews)) == 1
    assert len(reviews) == (2 if lost_receipt else 1)


def test_step_engine_preserves_manual_evidence_citation_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    contracts.test_manual_model_classifies_evidence_and_rejects_uncited_plain_text(tmp_path)
