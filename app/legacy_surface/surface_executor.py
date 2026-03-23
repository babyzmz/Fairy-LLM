from __future__ import annotations

from app.legacy_surface.surface_models import LegacySurfaceDecision
from app.runtime.system_actions.action_models import SystemActionResolution


class LegacySurfaceExecutor:
    def prepare(
        self,
        *,
        resolution: SystemActionResolution,
        explicit_intent: str,
        enabled: bool,
    ) -> dict:
        decision = LegacySurfaceDecision(
            action_name=resolution.name,
            reason=resolution.reason or "legacy_surface_automation",
            explicit_intent=str(explicit_intent or "").strip(),
            enabled=bool(enabled),
        )
        if decision.can_delegate:
            return {
                "_force_legacy_fallback": True,
                "_runtime_executor_path": "legacy_surface_legacy_executor",
                "_runtime_system_action_type": "legacy_only",
                "_runtime_system_action_name": resolution.name,
                "_legacy_reason": decision.reason,
                "_legacy_surface_automation": True,
            }
        if not enabled:
            message = (
                "Legacy desktop automation is disabled. "
                "Enable FAIRY_ENABLE_LEGACY_SURFACE=1 and use an explicit desktop automation command to allow it."
            )
        else:
            message = (
                "Legacy desktop automation is isolated from the main chat path. "
                "Use an explicit desktop automation command prefix to run it."
            )
        return {
            "assistant_text": message,
            "assistant_html": "",
            "summary": message,
            "sources": [],
            "warnings": ["legacy_surface_isolated"],
            "structured": {
                "card_type": "generic_info",
                "title": "Legacy Desktop Action",
                "summary": message,
                "fields": [
                    {"label": "Action", "value": resolution.name},
                    {"label": "Status", "value": "disabled" if not enabled else "explicit_intent_required"},
                ],
            },
            "skill_name": "system_action",
            "success": False,
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "cancelled": False,
            "errors": [{"code": "legacy_surface_disabled", "message": message}],
            "_runtime_executor_path": "legacy_surface_blocked",
            "_runtime_system_action_type": "legacy_only",
            "_runtime_system_action_name": resolution.name,
            "_legacy_surface_automation": True,
        }
