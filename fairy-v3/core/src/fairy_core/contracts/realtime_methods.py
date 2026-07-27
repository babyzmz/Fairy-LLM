from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from fairy_core.contracts.method_primitives import CoreMethod, CoreMethodTransport
from fairy_core.contracts.persona import (
    RealtimePersonaSnapshotInput,
    RealtimePersonaSnapshotModel,
)
from fairy_core.contracts.realtime import (
    CompanionDigestCreateInput,
    CompanionDigestGetInput,
    CompanionDigestListInput,
    CompanionSessionDigestModel,
    CompanionSessionDigestPageModel,
    GameMemoryDeleteResult,
    GameMemoryDigestModel,
    GameMemoryIdInput,
    GameMemoryListInput,
    GameMemoryPageModel,
    GameMemorySaveInput,
    RealtimeAssistanceCancelInput,
    RealtimeAssistanceGetInput,
    RealtimeAssistanceModel,
    RealtimeAssistanceRequestInput,
    RealtimeSessionIdInput,
    RealtimeSessionListInput,
    RealtimeSessionModel,
    RealtimeSessionPageModel,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeSessionStopInput,
    RealtimeTranscriptAppendInput,
    RealtimeTranscriptEntryModel,
    RealtimeTranscriptListInput,
    RealtimeTranscriptPageModel,
)

REALTIME_METHODS: Mapping[str, CoreMethod] = MappingProxyType(
    {
        "realtime.memories.delete": CoreMethod(
            "realtime.memories.delete",
            GameMemoryIdInput,
            GameMemoryDeleteResult,
        ),
        "realtime.memories.list": CoreMethod(
            "realtime.memories.list",
            GameMemoryListInput,
            GameMemoryPageModel,
        ),
        "realtime.memories.save": CoreMethod(
            "realtime.memories.save",
            GameMemorySaveInput,
            GameMemoryDigestModel,
        ),
        "realtime.digests.create": CoreMethod(
            "realtime.digests.create",
            CompanionDigestCreateInput,
            CompanionSessionDigestModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.digests.get": CoreMethod(
            "realtime.digests.get",
            CompanionDigestGetInput,
            CompanionSessionDigestModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.digests.list": CoreMethod(
            "realtime.digests.list",
            CompanionDigestListInput,
            CompanionSessionDigestPageModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.assistance.cancel": CoreMethod(
            "realtime.assistance.cancel",
            RealtimeAssistanceCancelInput,
            RealtimeAssistanceModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.assistance.get": CoreMethod(
            "realtime.assistance.get",
            RealtimeAssistanceGetInput,
            RealtimeAssistanceModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.assistance.request": CoreMethod(
            "realtime.assistance.request",
            RealtimeAssistanceRequestInput,
            RealtimeAssistanceModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.persona.snapshot": CoreMethod(
            "realtime.persona.snapshot",
            RealtimePersonaSnapshotInput,
            RealtimePersonaSnapshotModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.sessions.get": CoreMethod(
            "realtime.sessions.get",
            RealtimeSessionIdInput,
            RealtimeSessionModel,
        ),
        "realtime.sessions.list": CoreMethod(
            "realtime.sessions.list",
            RealtimeSessionListInput,
            RealtimeSessionPageModel,
        ),
        "realtime.sessions.report": CoreMethod(
            "realtime.sessions.report",
            RealtimeSessionReportInput,
            RealtimeSessionModel,
        ),
        "realtime.sessions.start": CoreMethod(
            "realtime.sessions.start",
            RealtimeSessionStartInput,
            RealtimeSessionModel,
        ),
        "realtime.sessions.stop": CoreMethod(
            "realtime.sessions.stop",
            RealtimeSessionStopInput,
            RealtimeSessionModel,
        ),
        "realtime.transcript.append": CoreMethod(
            "realtime.transcript.append",
            RealtimeTranscriptAppendInput,
            RealtimeTranscriptEntryModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
        "realtime.transcript.list": CoreMethod(
            "realtime.transcript.list",
            RealtimeTranscriptListInput,
            RealtimeTranscriptPageModel,
            CoreMethodTransport.LOCAL_ONLY,
        ),
    }
)


__all__ = ["REALTIME_METHODS"]
