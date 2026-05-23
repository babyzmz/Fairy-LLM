from __future__ import annotations

from dataclasses import dataclass

from app.runtime.system_actions.action_models import SystemActionResolution

EXPLICIT_DESKTOP_AUTOMATION_MARKERS: tuple[str, ...] = (
    "/desktop",
    "/surface",
    "desktop automation:",
    "surface automation:",
    "桌面自动化:",
)


def is_explicit_desktop_automation_command(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(lowered.startswith(marker) for marker in EXPLICIT_DESKTOP_AUTOMATION_MARKERS)


def strip_explicit_desktop_automation_prefix(text: str) -> str:
    raw = str(text or "").strip()
    lowered = raw.lower()
    for marker in EXPLICIT_DESKTOP_AUTOMATION_MARKERS:
        if lowered.startswith(marker):
            return raw[len(marker) :].strip()
    return raw


@dataclass(slots=True)
class DesktopAutomationCompatibilityDecision:
    action_name: str
    reason: str
    explicit_intent: str = ""
    enabled: bool = False

    @property
    def can_delegate(self) -> bool:
        return self.enabled and self.explicit_intent == "desktop_automation"


class DesktopAutomationCompatibilityExecutor:
    def prepare(
        self,
        *,
        resolution: SystemActionResolution,
        explicit_intent: str,
        enabled: bool,
    ) -> dict:
        decision = DesktopAutomationCompatibilityDecision(
            action_name=resolution.name,
            reason=resolution.reason or "desktop_automation_compatibility",
            explicit_intent=str(explicit_intent or "").strip(),
            enabled=bool(enabled),
        )
        if decision.can_delegate:
            return {
                "_force_bundle_fallback": True,
                "_runtime_executor_path": "desktop_automation_compatibility_executor",
                "_runtime_system_action_type": "desktop_automation_compatibility",
                "_runtime_system_action_name": resolution.name,
                "_bundle_reason": decision.reason,
                "_desktop_automation_compatibility": True,
            }
        if not enabled:
            message = (
                "Desktop automation compatibility mode is disabled. "
                "Enable FAIRY_ENABLE_DESKTOP_AUTOMATION_COMPAT=1 and use an explicit desktop automation command to allow it."
            )
        else:
            message = (
                "Desktop automation compatibility mode is isolated from the main chat path. "
                "Use an explicit desktop automation command prefix to run it."
            )
        return {
            "assistant_text": message,
            "assistant_html": "",
            "summary": message,
            "sources": [],
            "warnings": ["desktop_automation_compatibility_isolated"],
            "structured": {
                "card_type": "generic_info",
                "title": "Desktop Automation",
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
            "errors": [{"code": "desktop_automation_compatibility_disabled", "message": message}],
            "_runtime_executor_path": "desktop_automation_compatibility_blocked",
            "_runtime_system_action_type": "desktop_automation_compatibility",
            "_runtime_system_action_name": resolution.name,
            "_desktop_automation_compatibility": True,
        }
