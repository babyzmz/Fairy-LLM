from __future__ import annotations

from typing import Protocol


class AssistantRecovery(Protocol):
    def recover_orphaned_turns(self) -> tuple[object, ...]: ...


class RuntimeRecovery(Protocol):
    def recover_interrupted(self, *, verify_running: bool) -> tuple[object, ...]: ...


class MediaRecovery(Protocol):
    def recover_interrupted(self) -> dict[str, int]: ...


def recover_interrupted_work(
    *,
    assistant: AssistantRecovery,
    runtime: RuntimeRecovery | None,
    media: MediaRecovery | None,
    verify_running_previews: bool,
) -> dict[str, int]:
    turns = assistant.recover_orphaned_turns()
    previews = (
        runtime.recover_interrupted(verify_running=verify_running_previews)
        if runtime is not None
        else ()
    )
    media_result = (
        media.recover_interrupted()
        if media is not None
        else {"interrupted": 0, "resumable_videos": 0}
    )
    return {
        "assistant_turns": len(turns),
        "media_interrupted": media_result["interrupted"],
        "media_resumable_videos": media_result["resumable_videos"],
        "previews": len(previews),
    }


def close_resources(*resources: object | None) -> None:
    for resource in resources:
        if resource is None:
            continue
        close = getattr(resource, "close", None)
        if callable(close):
            close()


__all__ = ["close_resources", "recover_interrupted_work"]
