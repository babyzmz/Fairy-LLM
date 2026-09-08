from functools import partial

import pytest

from tests.assistant import test_approval_resume as contracts


@pytest.mark.parametrize("case", ["approved", "rejected", "replaced", "removed", "intent"])
def test_real_tool_nodes_preserve_existing_approval_contracts(tmp_path, monkeypatch, case):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )
    if case == "approved":
        contracts.test_standard_profile_approval_resumes_one_tool_effect_once(tmp_path)
    elif case == "rejected":
        contracts.test_rejected_tool_becomes_bounded_result_and_duplicate_decision_is_idempotent(
            tmp_path,
        )
    elif case == "intent":
        contracts.test_approval_cannot_reuse_authority_after_a_new_intent_revision(tmp_path)
    else:
        contracts.test_builtin_approval_does_not_authorize_a_changed_or_removed_definition(
            tmp_path, case,
        )
