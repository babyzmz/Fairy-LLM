from __future__ import annotations

import json

from fairy_core.transports.stdio import build_local_service


def test_terminal_digest_and_proposals_remain_scoped_after_four_hour_usage(tmp_path) -> None:
    service = build_local_service(tmp_path)
    try:
        started = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-phase-7-soak",
                "provider": "local_mini_cpm_o45",
                "locale": "en-AU",
                "microphone_consent": True,
                "screen_consent": True,
                "idempotency_key": "phase-7-soak-session",
            },
        )
        entries = []
        for index in range(30):
            entries.append(
                service.invoke(
                    "realtime.transcript.append",
                    {
                        "session_id": started["id"],
                        "speaker": "assistant",
                        "text": f"Public checkpoint {index}.",
                    },
                )
            )
        entries.append(
            service.invoke(
                "realtime.transcript.append",
                {
                    "session_id": started["id"],
                    "speaker": "user",
                    "text": "Finished the tutorial. Next goal is chapter one.",
                },
            )
        )
        active = service.invoke(
            "realtime.sessions.report",
            {
                "session_id": started["id"],
                "status": "active",
                "expected_revision": started["revision"],
                "audio_input_ms": 14_400_000,
                "audio_output_ms": 3_600_000,
                "video_frame_count": 14_400,
                "interruption_count": 10,
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
                "audio_input_ms": 14_400_000,
                "audio_output_ms": 3_600_000,
                "video_frame_count": 14_400,
                "interruption_count": 10,
            },
        )
        digest = service.invoke(
            "realtime.digests.create",
            {
                "session_id": started["id"],
                "request_id": "phase-7-soak-digest",
                "activity": "game",
                "subject_title": "Deterministic soak",
            },
        )
        proposals = service.invoke(
            "realtime.memory-proposals.list",
            {
                "session_id": started["id"],
                "digest_id": digest["id"],
                "pending_only": False,
                "limit": 100,
            },
        )["items"]

        assert completed["status"] == "completed"
        assert completed["audio_input_ms"] == 14_400_000
        assert digest["source_first_sequence"] == entries[0]["sequence"]
        assert digest["source_last_sequence"] == entries[-1]["sequence"]
        assert len(digest["activities"]) <= 12
        assert len(digest["progress_summary"]) <= 1_200
        assert {proposal["kind"] for proposal in proposals} == {
            "game_progress",
            "next_goal",
        }
        assert {proposal["status"] for proposal in proposals} == {"promoted"}
        assert all(proposal["session_id"] == started["id"] for proposal in proposals)
        assert all(proposal["digest_id"] == digest["id"] for proposal in proposals)
        assert all(
            digest["source_first_sequence"]
            <= proposal["source_first_sequence"]
            <= digest["source_last_sequence"]
            for proposal in proposals
        )
        serialized = json.dumps(
            {"digest": digest, "proposals": proposals},
            ensure_ascii=False,
            sort_keys=True,
        )
        for forbidden in (
            "api_key",
            "credential",
            "raw_media",
            "provider_hidden_item",
            "reasoning",
            "application_path",
        ):
            assert forbidden not in serialized

        other = service.invoke(
            "realtime.sessions.start",
            {
                "device_id": "desktop-phase-7-soak",
                "provider": "local_mini_cpm_o45",
                "locale": "en-AU",
                "microphone_consent": True,
                "idempotency_key": "phase-7-soak-other-session",
            },
        )
        assert (
            service.invoke(
                "realtime.digests.list",
                {"session_id": other["id"], "limit": 10},
            )["items"]
            == []
        )
    finally:
        service.close()
