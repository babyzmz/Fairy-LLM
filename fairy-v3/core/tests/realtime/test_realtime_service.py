from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import inspect

from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    VersionConflictError,
)
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory, create_sqlite_core_engine
from fairy_core.realtime.models import RealtimeAssistance
from fairy_core.realtime.repository import SqlAlchemyRealtimeRepository
from fairy_core.transports.stdio import build_local_service
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def test_realtime_persona_snapshot_uses_the_canonical_core_authority(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        snapshot = service.invoke(
            "realtime.persona.snapshot",
            {
                "locale": "zh-CN",
                "activity_profile": "game",
                "interaction_intensity": "standard",
                "current_goal": "Finish the contract freeze",
            },
        )
        assert snapshot["schema_version"] == 1
        assert snapshot["identity"]["name"] == "Fairy"
        assert len(snapshot["persona_digest"]) == 64
        assert snapshot["short_memory"]["current_goal"] == ("Finish the contract freeze")
        assert "system_prompt" not in snapshot
    finally:
        service.close()


def test_realtime_session_and_game_memory_round_trip(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "zh-CN",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "session-1",
            },
        )
        assert started["provider"] == "glm_realtime_flash"
        assert started["status"] == "starting"
        assert started["revision"] == 1

        replayed = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "zh-CN",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "session-1",
            },
        )
        assert replayed["id"] == started["id"]

        active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "active",
                "expected_revision": 1,
                "audio_input_ms": 500,
                "video_frame_count": 1,
            },
        )
        with pytest.raises(VersionConflictError):
            service.invoke(
                "realtime.sessions.stop",
                {"session_id": started["id"], "expected_revision": 1},
            )
        stopping = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": active["revision"]},
        )
        repeated_stop = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": active["revision"]},
        )
        assert repeated_stop == stopping
        completed = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "completed",
                "expected_revision": stopping["revision"],
                "audio_input_ms": 750,
                "audio_output_ms": 300,
                "video_frame_count": 2,
                "interruption_count": 1,
            },
        )
        assert completed["ended_at"] is not None
        terminal_stop = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": 1},
        )
        assert terminal_stop == completed

        memory = service.invoke(
            "realtime.memories.save",
            {
                "session_id": started["id"],
                "game_title": "A Test Game",
                "played_at": datetime.now(UTC).isoformat(),
                "duration_seconds": 60,
                "activities": ["Completed tutorial"],
                "progress_summary": "Finished the tutorial.",
                "next_goal": "Start chapter one.",
            },
        )
        assert memory["accepted"] is False
        projected = service.invoke(
            "realtime.digests.get",
            {"digest_id": memory["id"]},
        )
        assert projected["activity"] == "game"
        assert projected["policy_version"] == "legacy-game-memory-v1"
        assert service.invoke("realtime.memories.list", {})["items"][0]["id"] == memory["id"]
        assert (
            service.invoke("realtime.memories.delete", {"memory_id": memory["id"]})["deleted"]
            is True
        )
    finally:
        service.close()


def test_companion_digest_is_stable_only_idempotent_and_session_scoped(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-digest",
                "provider": "auto",
                "locale": "zh-CN",
                "microphone_consent": True,
                "screen_consent": True,
                "idempotency_key": "digest-session",
            },
        )
        first = service.invoke(
            "realtime.transcript.append",
            {
                "session_id": started["id"],
                "speaker": "user",
                "text": "Finished the tutorial. Next goal is chapter one.",
            },
        )
        second = service.invoke(
            "realtime.transcript.append",
            {
                "session_id": started["id"],
                "speaker": "assistant",
                "text": "教学关已经完成。",
            },
        )
        active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "active",
                "expected_revision": started["revision"],
            },
        )
        stopping = service.invoke(
            "realtime.sessions.stop",
            {
                "session_id": started["id"],
                "expected_revision": active["revision"],
            },
        )
        completed = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "completed",
                "expected_revision": stopping["revision"],
            },
        )
        digest = service.invoke(
            "realtime.digests.create",
            {
                "session_id": started["id"],
                "request_id": "digest-1",
                "activity": "game",
                "subject_title": "A Test Game",
            },
        )
        replay = service.invoke(
            "realtime.digests.create",
            {
                "session_id": started["id"],
                "request_id": "digest-1",
                "activity": "game",
                "subject_title": "A Test Game",
            },
        )

        assert replay["id"] == digest["id"]
        assert digest["conversation_id"] == started["conversation_id"]
        assert digest["source_first_sequence"] == first["sequence"]
        assert digest["source_last_sequence"] == second["sequence"]
        assert len(digest["source_digest"]) == 64
        assert digest["next_goal"] == "Finished the tutorial. Next goal is chapter one."
        assert digest["progress_summary"] == "Finished the tutorial. Next goal is chapter one."
        assert len(digest["proposal_ids"]) == 2
        proposals = service.invoke(
            "realtime.memory-proposals.list",
            {"digest_id": digest["id"]},
        )["items"]
        assert {item["kind"] for item in proposals} == {
            "game_progress",
            "next_goal",
        }
        assert {item["status"] for item in proposals} == {"promoted"}
        assert all(item["claim_id"] for item in proposals)
        assert (
            service.invoke(
                "realtime.digests.get",
                {"digest_id": digest["id"]},
            )
            == digest
        )
        listed = service.invoke(
            "realtime.digests.list",
            {"session_id": started["id"]},
        )
        assert [item["id"] for item in listed["items"]] == [digest["id"]]

        with pytest.raises(IdempotencyConflictError):
            service.invoke(
                "realtime.digests.create",
                {
                    "session_id": started["id"],
                    "request_id": "digest-1",
                    "activity": "focus",
                    "subject_title": "A Test Game",
                },
            )
        with pytest.raises(InvalidTransitionError):
            service.invoke(
                "realtime.transcript.append",
                {
                    "session_id": started["id"],
                    "speaker": "user",
                    "text": "This late caption must not mutate digest evidence.",
                },
            )
        assert completed["status"] == "completed"
    finally:
        service.close()


def test_realtime_memory_requires_confirmation_for_inference_and_blocks_secrets(
    tmp_path,
) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-memory-policy",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "idempotency_key": "memory-policy-session",
            },
        )
        for text in (
            "I usually work late.",
            "I feel more productive in quiet rooms.",
            "Remember that I prefer concise answers.",
            "Remember password=supersecretvalue.",
        ):
            service.invoke(
                "realtime.transcript.append",
                {
                    "session_id": started["id"],
                    "speaker": "user",
                    "text": text,
                },
            )
        active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "active",
                "expected_revision": started["revision"],
            },
        )
        stopping = service.invoke(
            "realtime.sessions.stop",
            {
                "session_id": started["id"],
                "expected_revision": active["revision"],
            },
        )
        service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "completed",
                "expected_revision": stopping["revision"],
            },
        )
        digest = service.invoke(
            "realtime.digests.create",
            {
                "session_id": started["id"],
                "request_id": "memory-policy-digest",
                "activity": "focus",
            },
        )
        proposals = service.invoke(
            "realtime.memory-proposals.list",
            {"digest_id": digest["id"]},
        )["items"]

        assert len(proposals) == 3
        explicit = next(item for item in proposals if item["kind"] == "explicit_preference")
        pending = [item for item in proposals if item["kind"] == "inferred_fact"]
        assert explicit["status"] == "promoted"
        assert explicit["policy_decision"] == "auto_promote"
        assert len(pending) == 2
        assert {item["status"] for item in pending} == {"pending"}
        assert all(item["policy_decision"] == "requires_confirmation" for item in pending)
        assert all("supersecretvalue" not in item["normalized_text"] for item in proposals)

        with pytest.raises(ValueError, match="explicit confirmation"):
            service.invoke(
                "realtime.memory-proposals.accept",
                {
                    "proposal_id": pending[0]["id"],
                    "expected_revision": pending[0]["revision"],
                    "user_confirmed": False,
                    "idempotency_key": "accept-inferred",
                },
            )
        accepted = service.invoke(
            "realtime.memory-proposals.accept",
            {
                "proposal_id": pending[0]["id"],
                "expected_revision": pending[0]["revision"],
                "user_confirmed": True,
                "idempotency_key": "accept-inferred",
            },
        )
        replayed = service.invoke(
            "realtime.memory-proposals.accept",
            {
                "proposal_id": pending[0]["id"],
                "expected_revision": pending[0]["revision"],
                "user_confirmed": True,
                "idempotency_key": "accept-inferred",
            },
        )
        assert replayed == accepted
        assert accepted["status"] == "promoted"
        assert accepted["claim_id"] is not None

        rejected = service.invoke(
            "realtime.memory-proposals.reject",
            {
                "proposal_id": pending[1]["id"],
                "expected_revision": pending[1]["revision"],
                "user_confirmed": True,
                "idempotency_key": "reject-inferred",
            },
        )
        assert rejected["status"] == "rejected"
        assert rejected["claim_id"] is None
        assert (
            service.invoke(
                "realtime.memory-proposals.list",
                {"digest_id": digest["id"], "pending_only": True},
            )["items"]
            == []
        )
    finally:
        service.close()

    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    try:
        with factory() as unit_of_work:
            revisions = unit_of_work.memory.revisions_for_claim(UUID(accepted["claim_id"]))
        assert revisions[-1].authority.value == "explicit_user"
        assert revisions[-1].source_observation_ids == ()
        assert len(revisions[-1].source_event_ids) == 1
    finally:
        engine.dispose()


def test_realtime_memory_conflict_stays_pending_until_user_accepts(tmp_path) -> None:
    service = build_local_service(tmp_path)

    def create_goal(suffix: str, text: str) -> dict:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-shared-goal",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "idempotency_key": f"goal-session-{suffix}",
            },
        )
        service.invoke(
            "realtime.transcript.append",
            {
                "session_id": started["id"],
                "speaker": "user",
                "text": text,
            },
        )
        active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "active",
                "expected_revision": started["revision"],
            },
        )
        stopping = service.invoke(
            "realtime.sessions.stop",
            {
                "session_id": started["id"],
                "expected_revision": active["revision"],
            },
        )
        service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "completed",
                "expected_revision": stopping["revision"],
            },
        )
        digest = service.invoke(
            "realtime.digests.create",
            {
                "session_id": started["id"],
                "request_id": f"goal-digest-{suffix}",
                "activity": "focus",
            },
        )
        proposals = service.invoke(
            "realtime.memory-proposals.list",
            {"digest_id": digest["id"]},
        )["items"]
        return next(item for item in proposals if item["kind"] == "next_goal")

    try:
        first = create_goal("one", "Next goal is to finish chapter one.")
        second = create_goal("two", "Next goal is to finish chapter two.")

        assert first["status"] == "promoted"
        assert second["status"] == "pending"
        assert second["policy_decision"] == "requires_confirmation"
        assert second["policy_reason"] == "existing_claim_conflict"

        accepted = service.invoke(
            "realtime.memory-proposals.accept",
            {
                "proposal_id": second["id"],
                "expected_revision": second["revision"],
                "user_confirmed": True,
                "idempotency_key": "accept-new-goal",
            },
        )
        assert accepted["status"] == "promoted"
        assert accepted["claim_id"] == first["claim_id"]
    finally:
        service.close()

    engine = create_sqlite_core_engine(tmp_path / "core.db")
    local = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")
    try:
        with local() as unit_of_work:
            revisions = unit_of_work.memory.revisions_for_claim(UUID(first["claim_id"]))
            assert [revision.revision for revision in revisions] == [1, 2]
            assert revisions[-1].normalized_text.endswith("chapter two.")
            assert revisions[-1].authority.value == "explicit_user"
        with other() as unit_of_work:
            assert unit_of_work.realtime.get_memory_proposal(UUID(second["id"])) is None
    finally:
        engine.dispose()


def test_companion_digest_requires_transcript_and_memory_policy(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-empty-digest",
                "provider": "auto",
                "locale": "en-AU",
                "memory_mode": "none",
                "microphone_consent": True,
                "idempotency_key": "empty-digest-session",
            },
        )
        cancelled = service.invoke(
            "realtime.sessions.stop",
            {
                "session_id": started["id"],
                "expected_revision": started["revision"],
            },
        )
        with pytest.raises(ValueError, match="disabled"):
            service.invoke(
                "realtime.digests.create",
                {
                    "session_id": started["id"],
                    "request_id": "digest-disabled",
                },
            )
        assert cancelled["status"] == "cancelled"

        enabled = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-empty-enabled",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "idempotency_key": "empty-enabled-session",
            },
        )
        service.invoke(
            "realtime.sessions.stop",
            {
                "session_id": enabled["id"],
                "expected_revision": enabled["revision"],
            },
        )
        with pytest.raises(ValueError, match="stable public transcript"):
            service.invoke(
                "realtime.digests.create",
                {
                    "session_id": enabled["id"],
                    "request_id": "digest-empty",
                },
            )

        no_memory = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-core-memory-disabled",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "idempotency_key": "core-memory-disabled-session",
            },
        )
        service.invoke(
            "realtime.transcript.append",
            {
                "session_id": no_memory["id"],
                "speaker": "user",
                "text": "Remember that I prefer concise answers.",
            },
        )
        no_memory_active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": no_memory["id"],
                "status": "active",
                "expected_revision": no_memory["revision"],
            },
        )
        no_memory_stopping = service.invoke(
            "realtime.sessions.stop",
            {
                "session_id": no_memory["id"],
                "expected_revision": no_memory_active["revision"],
            },
        )
        service.invoke(
            "realtime.sessions.report",
            {
                "session_id": no_memory["id"],
                "status": "completed",
                "expected_revision": no_memory_stopping["revision"],
            },
        )
        service.invoke(
            "memory.settings.update",
            {
                "enabled": False,
                "retention_days": 365,
                "export_to_obsidian": False,
                "sync_normalized_content": False,
                "expected_revision": 0,
                "idempotency_key": "disable-memory-for-digest",
            },
        )
        digest = service.invoke(
            "realtime.digests.create",
            {
                "session_id": no_memory["id"],
                "request_id": "digest-with-core-memory-disabled",
            },
        )
        assert digest["proposal_ids"] == []
    finally:
        service.close()


def test_realtime_assistance_repository_is_idempotent_and_revision_fenced(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-assistance",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "idempotency_key": "assistance-session",
            },
        )
    finally:
        service.close()

    engine = create_sqlite_core_engine(tmp_path / "core.db")
    local = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other")
    request = RealtimeAssistance.create(
        session_id=started["id"],
        conversation_id=started["conversation_id"],
        request_id="guide-1",
        segment_id="segment-1",
        context_epoch=1,
        question="Where is the hidden boss?",
        activity_profile="game",
        application_title="Test Game",
        observed_facts=("Map is open",),
        allow_network=True,
        locale="en-AU",
    )
    try:
        with local() as unit_of_work:
            saved = unit_of_work.realtime.add_assistance(request)
            unit_of_work.commit()

        with local() as unit_of_work:
            replay = unit_of_work.realtime.add_assistance(
                RealtimeAssistance.create(
                    session_id=request.session_id,
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    segment_id=request.segment_id,
                    context_epoch=request.context_epoch,
                    question=request.question,
                    activity_profile=request.activity_profile,
                    application_title=request.application_title,
                    observed_facts=request.observed_facts,
                    allow_network=request.allow_network,
                    locale=request.locale,
                )
            )
            assert replay.id == saved.id
            assert (
                unit_of_work.realtime.nonterminal_assistance_for_session(request.session_id).id
                == saved.id
            )

            running = saved.start(task_id=saved.id, turn_id=request.session_id)
            unit_of_work.realtime.update_assistance(
                running,
                expected_revision=saved.revision,
            )
            with pytest.raises(VersionConflictError):
                unit_of_work.realtime.update_assistance(
                    running,
                    expected_revision=saved.revision,
                )
            unit_of_work.commit()

        with local() as unit_of_work, pytest.raises(IdempotencyConflictError):
            unit_of_work.realtime.add_assistance(
                RealtimeAssistance.create(
                    session_id=request.session_id,
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    segment_id=request.segment_id,
                    context_epoch=request.context_epoch,
                    question="A different question",
                    activity_profile=request.activity_profile,
                    application_title=request.application_title,
                    observed_facts=request.observed_facts,
                    allow_network=request.allow_network,
                    locale=request.locale,
                )
            )

        with other() as unit_of_work:
            assert unit_of_work.realtime.get_assistance(saved.id) is None
    finally:
        engine.dispose()


def test_local_realtime_session_uses_the_pinned_audit_model(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-local",
                "provider": "local_mini_cpm_o45",
                "locale": "zh-CN",
                "voice_mode": "fairy",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "local-session-1",
            },
        )
        assert started["provider"] == "local_mini_cpm_o45"
        assert started["model_id"] == "openbmb/minicpm-o-4.5-fairy-beta@4.5-q4-502eec5"
    finally:
        service.close()


def test_voice_start_links_a_scratch_conversation(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "voice-1",
            },
        )
        # Starting voice auto-links a fresh conversation.
        assert started["conversation_id"] is not None

        # An idempotent replay reuses the session and its conversation.
        replay = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "voice-1",
            },
        )
        assert replay["id"] == started["id"]
        assert replay["conversation_id"] == started["conversation_id"]

        # A new session gets its own conversation.
        other = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "voice-2",
            },
        )
        assert other["conversation_id"] is not None
        assert other["conversation_id"] != started["conversation_id"]
    finally:
        service.close()


def test_voice_start_rolls_back_scratch_state_when_session_save_fails(
    tmp_path, monkeypatch
) -> None:
    def fail_session_save(self, session):
        raise RuntimeError("injected realtime session failure")

    monkeypatch.setattr(
        SqlAlchemyRealtimeRepository,
        "add_session",
        fail_session_save,
    )
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="injected realtime session failure"):
            service.invoke(
                "realtime.sessions.start",
                {
                    "device_id": "desktop-1",
                    "provider": "auto",
                    "locale": "en-AU",
                    "microphone_consent": True,
                    "screen_consent": True,
                    "game_audio_consent": False,
                    "idempotency_key": "voice-session-failure",
                },
            )

        assert service.invoke("conversations.list", {})["items"] == []
        assert service.invoke("realtime.sessions.list", {})["items"] == []
        _assert_no_scratch_artifacts(tmp_path)
    finally:
        service.close()


def test_voice_start_rolls_back_scratch_state_when_workspace_creation_fails(
    tmp_path, monkeypatch
) -> None:
    original_create = FileSystemWorkspaceProvisioner.create_initial_version

    def fail_after_workspace_creation(self, project_id, version_id, *, source=None):
        original_create(self, project_id, version_id, source=source)
        raise RuntimeError("injected workspace creation failure")

    monkeypatch.setattr(
        FileSystemWorkspaceProvisioner,
        "create_initial_version",
        fail_after_workspace_creation,
    )
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="injected workspace creation failure"):
            service.invoke(
                "realtime.sessions.start",
                {
                    "device_id": "desktop-1",
                    "provider": "auto",
                    "locale": "en-AU",
                    "microphone_consent": True,
                    "screen_consent": True,
                    "game_audio_consent": False,
                    "idempotency_key": "voice-workspace-failure",
                },
            )

        assert service.invoke("conversations.list", {})["items"] == []
        assert service.invoke("realtime.sessions.list", {})["items"] == []
        _assert_no_scratch_artifacts(tmp_path)
    finally:
        service.close()


def test_voice_transcript_append_and_list(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "auto",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "game_audio_consent": False,
                "idempotency_key": "transcript-1",
            },
        )
        conversation_id = started["conversation_id"]
        assert conversation_id is not None

        first = service.invoke(
            "realtime.transcript.append",
            {"session_id": started["id"], "speaker": "user", "text": "Where is the boss?"},
        )
        second = service.invoke(
            "realtime.transcript.append",
            {"session_id": started["id"], "speaker": "assistant", "text": "Behind the door."},
        )
        # Sequences are server-allocated and monotonic, linked to the conversation.
        assert (first["sequence"], second["sequence"]) == (1, 2)
        assert first["conversation_id"] == conversation_id

        page = service.invoke("realtime.transcript.list", {"conversation_id": conversation_id})
        assert [(e["speaker"], e["text"]) for e in page["items"]] == [
            ("user", "Where is the boss?"),
            ("assistant", "Behind the door."),
        ]
        transcript_events = [
            event
            for event in service.invoke("events.list", {"cursor": 0, "limit": 100})["items"]
            if event["event_type"] == "realtime.transcript.appended"
        ]
        assert [event["conversation_id"] for event in transcript_events] == [
            conversation_id,
            conversation_id,
        ]
        assert [event["payload"] for event in transcript_events] == [
            {
                "session_id": started["id"],
                "conversation_id": conversation_id,
                "entry_id": first["id"],
                "sequence": 1,
            },
            {
                "session_id": started["id"],
                "conversation_id": conversation_id,
                "entry_id": second["id"],
                "sequence": 2,
            },
        ]
        assert "Where is the boss?" not in str(transcript_events)
        assert "Behind the door." not in str(transcript_events)

        # An unrelated conversation has no transcript.
        empty = service.invoke(
            "realtime.transcript.list",
            {"conversation_id": "01900000-0000-7000-8000-0000000000ff"},
        )
        assert empty["items"] == []

        # Appending against an unknown session is rejected, never silently dropped.
        with pytest.raises(KeyError):
            service.invoke(
                "realtime.transcript.append",
                {
                    "session_id": "01900000-0000-7000-8000-0000000000aa",
                    "speaker": "user",
                    "text": "Nobody is listening.",
                },
            )
    finally:
        service.close()


def test_starting_session_stop_is_cancelled_and_idempotent(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "glm_realtime_flash",
                "microphone_consent": True,
                "screen_consent": True,
                "idempotency_key": "session-cancel-starting",
            },
        )

        cancelled = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": started["revision"]},
        )
        replayed = service.invoke(
            "realtime.sessions.stop",
            {"session_id": started["id"], "expected_revision": started["revision"]},
        )

        assert cancelled["status"] == "cancelled"
        assert cancelled["ended_at"] is not None
        assert replayed == cancelled
    finally:
        service.close()


def test_memory_cannot_be_saved_while_session_is_active(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-1",
                "provider": "gemini_live",
                "microphone_consent": True,
                "idempotency_key": "session-active",
            },
        )
        with pytest.raises(InvalidTransitionError):
            service.invoke(
                "realtime.memories.save",
                {
                    "session_id": started["id"],
                    "game_title": "A Test Game",
                    "played_at": datetime.now(UTC).isoformat(),
                    "duration_seconds": 10,
                    "activities": [],
                    "progress_summary": "Not finished.",
                },
            )
    finally:
        service.close()


def test_realtime_persistence_has_no_raw_context_columns(tmp_path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "privacy.db")
    try:
        inspector = inspect(engine)
        session_columns = {
            column["name"] for column in inspector.get_columns("core_realtime_sessions")
        }
        memory_columns = {
            column["name"] for column in inspector.get_columns("core_game_memory_observations")
        }
        forbidden = {
            "transcript",
            "audio",
            "audio_blob",
            "video",
            "frame",
            "frames",
            "provider_context",
            "conversation_items",
            "vad_events",
            "reasoning",
        }
        assert session_columns.isdisjoint(forbidden)
        assert memory_columns.isdisjoint(forbidden)
    finally:
        engine.dispose()


def _assert_no_scratch_artifacts(data_dir: Path) -> None:
    engine = create_sqlite_core_engine(data_dir / "core.db")
    try:
        with engine.connect() as connection:
            for table in (
                "core_conversations",
                "core_workspaces",
                "core_versions",
                "core_realtime_sessions",
            ):
                count = connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
                assert count == 0, table
    finally:
        engine.dispose()
    projects_root = data_dir / "workspaces" / "projects"
    assert not projects_root.exists() or not any(projects_root.iterdir())
