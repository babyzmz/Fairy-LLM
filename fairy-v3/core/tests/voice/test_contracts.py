from __future__ import annotations

from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.contracts.models import VoiceSynthesizeInput, VoiceTranscribeInput
from fairy_core.contracts.voice_sessions import VoiceSessionStartInput


def test_voice_contracts_are_public_and_do_not_accept_arbitrary_synthesis_text() -> None:
    assert CORE_METHODS["voice.transcribe"].request_model is VoiceTranscribeInput
    assert CORE_METHODS["voice.synthesize"].request_model is VoiceSynthesizeInput
    assert "text" not in VoiceSynthesizeInput.model_fields
    assert {
        "task_id",
        "turn_id",
        "message_id",
        "start_offset",
        "end_offset",
    }.issubset(VoiceSynthesizeInput.model_fields)
    assert CORE_METHODS["voice.sessions.start"].request_model is VoiceSessionStartInput
    assert "text" not in VoiceSessionStartInput.model_fields
    assert "profile_id" not in VoiceSessionStartInput.model_fields
