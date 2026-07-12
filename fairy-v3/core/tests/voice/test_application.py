from __future__ import annotations

import base64
from pathlib import Path

import pytest

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.models import Message, MessageRole, MessageVisibility
from fairy_core.commanding import EventVisibility
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import RiskLevel, build_default_registry
from fairy_core.contracts.models import (
    ExecutionTarget,
    TaskCreate,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
)
from fairy_core.contracts.voice_sessions import VoiceSessionIdInput, VoiceSessionStartInput
from fairy_core.domain.errors import CapabilityUnavailableError
from fairy_core.domain.models import OperationMode, WorkspaceType
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import (
    CancellationToken,
    ProviderCapability,
    ProviderKind,
    ProviderProfile,
)
from fairy_core.voice import (
    AudioMediaType,
    SynthesizedAudio,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegment,
    VoiceRegistry,
    VoiceSynthesisRequest,
)
from fairy_core.voice.application import VoiceApplication
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


def test_transcription_is_conversation_bound_and_decodes_audio_strictly(tmp_path: Path) -> None:
    fixture = voice_fixture(tmp_path)
    request = VoiceTranscribeInput(
        conversation_id=fixture.conversation_id,
        profile_id="voice",
        media_type=AudioMediaType.WEBM,
        audio_base64=base64.b64encode(b"recording").decode("ascii"),
        language="en",
    )

    result = fixture.application.transcribe(request)

    assert result.conversation_id == fixture.conversation_id
    assert result.text == "Hello Fairy"
    assert fixture.provider.transcriptions[0].audio == b"recording"
    with pytest.raises(ValueError, match="base64"):
        fixture.application.transcribe(request.model_copy(update={"audio_base64": "***"}))
    with pytest.raises(KeyError, match="conversation"):
        fixture.application.transcribe(
            request.model_copy(update={"conversation_id": "00000000-0000-4000-8000-000000000099"})
        )


def test_synthesis_reads_only_a_public_assistant_message_slice(tmp_path: Path) -> None:
    fixture = voice_fixture(tmp_path)
    request = VoiceSynthesizeInput(
        task_id=fixture.task_id,
        turn_id=fixture.turn_id,
        message_id=fixture.assistant_message_id,
        profile_id="voice",
        voice="alloy",
        start_offset=0,
        end_offset=6,
    )

    result = fixture.application.synthesize(request)

    assert result.message_id == fixture.assistant_message_id
    assert result.media_type == "audio/wav"
    assert base64.b64decode(result.audio_base64) == fixture.provider.wav
    assert fixture.provider.syntheses[0].text == "First."

    with pytest.raises(ValueError, match="assistant"):
        fixture.application.synthesize(
            request.model_copy(update={"message_id": fixture.user_message_id})
        )


def test_native_voice_session_is_scope_bound_idempotent_and_cancellable(tmp_path: Path) -> None:
    fixture = voice_fixture(tmp_path)
    request = VoiceSessionStartInput(
        task_id=fixture.task_id,
        turn_id=fixture.turn_id,
        message_id=fixture.assistant_message_id,
        start_offset=0,
        end_offset=6,
        idempotency_key="voice:session:first",
    )

    session = fixture.application.start_session(request)

    assert session.validated_text == "First."
    assert session.status == "prepared"
    assert len(session.scope_digest) == 64
    assert fixture.application.start_session(request).id == session.id
    assert fixture.application.get_session(VoiceSessionIdInput(session_id=session.id)) == session

    cancelled = fixture.application.cancel_session(
        VoiceSessionIdInput(session_id=session.id)
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None
    assert (
        fixture.application.cancel_session(VoiceSessionIdInput(session_id=session.id))
        == cancelled
    )
    with pytest.raises(ValueError, match="idempotency"):
        fixture.application.start_session(
            request.model_copy(update={"start_offset": 7, "end_offset": 13})
        )
    with pytest.raises(ValueError, match="assistant"):
        fixture.application.start_session(
            request.model_copy(
                update={
                    "message_id": fixture.user_message_id,
                    "idempotency_key": "voice:session:user",
                }
            )
        )
    with pytest.raises(ValueError, match="range"):
        fixture.application.synthesize(
            request.model_copy(update={"start_offset": 0, "end_offset": 1000})
        )


def test_streaming_voice_session_reconstructs_only_durable_public_deltas(tmp_path: Path) -> None:
    fixture = voice_fixture(tmp_path)
    with fixture.unit_of_work_factory() as unit_of_work:
        task = unit_of_work.state.get_task(fixture.task_id)
        assert task is not None
        run = unit_of_work.commands.create_run(
            command_name="model.generate",
            actor="assistant",
            scope=fixture.core.scope_for_task(unit_of_work.state, task),
            input_payload={"turn_id": str(fixture.turn_id)},
            risk_level=RiskLevel.LOW,
            idempotency_key="voice:streaming:run",
        )
        for chunk_index, text in enumerate(("Live ", "sentence."), start=1):
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="assistant.message.delta",
                visibility=EventVisibility.USER,
                message="Assistant response updated",
                payload={
                    "turn_id": str(fixture.turn_id),
                    "model_round": 0,
                    "chunk_index": chunk_index,
                    "text": text,
                },
            )
        unit_of_work.commit()

    session = fixture.application.start_session(
        VoiceSessionStartInput(
            task_id=fixture.task_id,
            turn_id=fixture.turn_id,
            message_id=None,
            start_offset=0,
            end_offset=14,
            idempotency_key="voice:streaming:sentence",
        )
    )

    assert session.message_id is None
    assert session.validated_text == "Live sentence."
    assert session.source_cursor > 0
    with pytest.raises(ValueError, match="durable assistant output"):
        fixture.application.start_session(
            VoiceSessionStartInput(
                task_id=fixture.task_id,
                turn_id=fixture.turn_id,
                message_id=None,
                start_offset=0,
                end_offset=15,
                idempotency_key="voice:streaming:untrusted-tail",
            )
        )


def test_unavailable_voice_provider_uses_the_stable_capability_error(tmp_path: Path) -> None:
    fixture = voice_fixture(tmp_path)
    unavailable = VoiceApplication(
        unit_of_work_factory=fixture.unit_of_work_factory,
        registry=VoiceRegistry(),
    )

    with pytest.raises(CapabilityUnavailableError) as captured:
        unavailable.transcribe(
            VoiceTranscribeInput(
                conversation_id=fixture.conversation_id,
                profile_id="missing",
                media_type=AudioMediaType.WEBM,
                audio_base64=base64.b64encode(b"recording").decode("ascii"),
                language=None,
            )
        )

    assert captured.value.code == "CAPABILITY_NOT_AVAILABLE"


class FixtureVoiceProvider:
    def __init__(self) -> None:
        self.profile = ProviderProfile.create(
            profile_id="voice",
            display_name="Voice",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://voice.example.test/v1",
            model_id="audio-model",
            capabilities=frozenset(
                {
                    ProviderCapability.TEXT,
                    ProviderCapability.STT,
                    ProviderCapability.TTS,
                }
            ),
            credential_ref=None,
            fallback_profile_id=None,
            timeout_seconds=30,
            enabled=True,
        )
        self.credential_configured = True
        self.transcriptions: list[TranscriptionRequest] = []
        self.syntheses: list[VoiceSynthesisRequest] = []
        self.wav = pcm_wav(samples=b"\x00\x00\x01\x00")

    def transcribe(
        self,
        request: TranscriptionRequest,
        cancellation: CancellationToken,
    ) -> TranscriptionResult:
        cancellation.raise_if_cancelled()
        self.transcriptions.append(request)
        return TranscriptionResult.create(
            profile_id=request.profile_id,
            text="Hello Fairy",
            language="en",
            segments=(
                TranscriptSegment(
                    index=0,
                    text="Hello Fairy",
                    start_seconds=0,
                    end_seconds=0.8,
                ),
            ),
        )

    def synthesize(
        self,
        request: VoiceSynthesisRequest,
        cancellation: CancellationToken,
    ) -> SynthesizedAudio:
        cancellation.raise_if_cancelled()
        self.syntheses.append(request)
        return SynthesizedAudio.create(
            profile_id=request.profile_id,
            wav=self.wav,
            sample_rate=24_000,
            channels=1,
            frames=2,
        )


class VoiceFixture:
    def __init__(
        self,
        *,
        application: VoiceApplication,
        unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
        provider: FixtureVoiceProvider,
        conversation_id,
        task_id,
        turn_id,
        user_message_id,
        assistant_message_id,
        core,
    ) -> None:
        self.application = application
        self.unit_of_work_factory = unit_of_work_factory
        self.provider = provider
        self.conversation_id = conversation_id
        self.task_id = task_id
        self.turn_id = turn_id
        self.user_message_id = user_message_id
        self.assistant_message_id = assistant_message_id
        self.core = core


def voice_fixture(tmp_path: Path) -> VoiceFixture:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    registry = build_default_registry()
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / "managed"),
        registry=registry,
        policy=PolicyEngine(registry),
    )
    conversation = core.create_conversation(
        project_id=None,
        workspace_type=WorkspaceType.CHAT_SCRATCH,
    )
    task = core.create_task(
        TaskCreate(
            conversation_id=conversation.id,
            user_request="Say the response",
            operation_mode=OperationMode.ANSWER,
            execution_target=ExecutionTarget.LOCAL,
            idempotency_key="voice:task",
        )
    ).task
    ledger = AssistantLedgerApplication(
        unit_of_work_factory=factory,
        scope_resolver=core.scope_for_task,
    )
    turn = ledger.create_turn(
        task_id=task.id,
        profile_id="voice",
        idempotency_key="voice:turn",
    )
    with factory() as unit_of_work:
        persisted = unit_of_work.assistant.get_turn(turn.id)
        assert persisted is not None
        expected_status = persisted.status
        expected_revision = persisted.cancellation_revision
        persisted.start()
        unit_of_work.assistant.update_turn(
            persisted,
            expected_status=expected_status,
            expected_cancellation_revision=expected_revision,
        )
        expected_status = persisted.status
        persisted.complete(usage={})
        unit_of_work.assistant.update_turn(
            persisted,
            expected_status=expected_status,
            expected_cancellation_revision=expected_revision,
        )
        assistant = Message.create(
            conversation_id=conversation.id,
            task_id=task.id,
            turn_id=turn.id,
            sequence=unit_of_work.assistant.next_message_sequence(conversation.id),
            role=MessageRole.ASSISTANT,
            visibility=MessageVisibility.USER,
            content="First. Second sentence.",
        )
        unit_of_work.assistant.append_message(assistant)
        user_message = unit_of_work.assistant.list_messages(
            conversation_id=conversation.id,
            limit=100,
            cursor=None,
        ).items[0]
        unit_of_work.commit()
    provider = FixtureVoiceProvider()
    application = VoiceApplication(
        unit_of_work_factory=factory,
        registry=VoiceRegistry((provider,)),
    )
    return VoiceFixture(
        application=application,
        unit_of_work_factory=factory,
        provider=provider,
        conversation_id=conversation.id,
        task_id=task.id,
        turn_id=turn.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant.id,
        core=core,
    )


def pcm_wav(*, samples: bytes) -> bytes:
    sample_rate = 24_000
    channels = 1
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    fmt = (
        (1).to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + (16).to_bytes(2, "little")
    )
    body = b"fmt " + len(fmt).to_bytes(4, "little") + fmt
    body += b"data" + len(samples).to_bytes(4, "little") + samples
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WAVE" + body
