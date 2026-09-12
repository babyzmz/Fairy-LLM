from __future__ import annotations

from fairy_core.contracts.method_primitives import CoreMethod
from fairy_core.contracts.models import (
    VoiceAudioModel,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
    VoiceTranscriptModel,
)
from fairy_core.contracts.voice_sessions import (
    VoiceSessionIdInput,
    VoiceSessionModel,
    VoiceSessionStartInput,
)

VOICE_METHODS = {
    "voice.synthesize": CoreMethod(
        "voice.synthesize",
        VoiceSynthesizeInput,
        VoiceAudioModel,
    ),
    "voice.sessions.cancel": CoreMethod(
        "voice.sessions.cancel",
        VoiceSessionIdInput,
        VoiceSessionModel,
    ),
    "voice.sessions.get": CoreMethod(
        "voice.sessions.get",
        VoiceSessionIdInput,
        VoiceSessionModel,
    ),
    "voice.sessions.start": CoreMethod(
        "voice.sessions.start",
        VoiceSessionStartInput,
        VoiceSessionModel,
    ),
    "voice.transcribe": CoreMethod(
        "voice.transcribe",
        VoiceTranscribeInput,
        VoiceTranscriptModel,
    ),
}
