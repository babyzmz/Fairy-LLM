from __future__ import annotations

import logging
from typing import Any

from app.capabilities.screen_capability import ScreenCapability
from app.models.skill_result import SkillResult
from app.models.tool_result import ToolResult
from app.skills.skill_spec import SkillSpec


logger = logging.getLogger(__name__)


class ScreenUnderstandingSkill:
    SPEC = SkillSpec(
        name="screen_understanding_skill",
        description="Read the current screen or foreground window and explain the UI.",
        trigger_hints=("\u5c4f\u5e55", "\u7a97\u53e3", "\u754c\u9762", "\u70b9\u54ea\u91cc", "\u4e0b\u4e00\u6b65"),
        allowed_tools=(
            "capture_screen",
            "capture_active_window",
            "get_active_app",
            "get_accessibility_tree",
            "describe_visible_regions",
        ),
        execution_steps=(
            "capture",
            "inspect_active_app",
            "load_accessibility_tree",
            "describe_visible_regions",
            "summarize_next_step",
        ),
        output_schema=("current_app", "screen_summary", "important_regions", "actionable_elements", "suggested_next_step"),
    )

    def __init__(self, screen: ScreenCapability, llm_helper: Any) -> None:
        self.screen = screen
        self.llm_helper = llm_helper

    def execute(self, user_request: str, allowed_tools: list[str], *, memory_context: str = "") -> SkillResult:
        tool_results: list[ToolResult] = []

        active_app = self.screen.get_active_app(allowed_tools=allowed_tools)
        tool_results.append(ToolResult("get_active_app", ok=True, data=active_app))
        logger.info(
            "active_app_detected app=%s context_app=%s",
            active_app.get("app_name", "Unknown"),
            (active_app.get("context_app") or {}).get("app_name", ""),
        )

        use_full_screen = self._should_capture_full_screen(user_request, active_app)
        if use_full_screen:
            capture = self.screen.capture_screen(allowed_tools=allowed_tools)
            tool_results.append(ToolResult("capture_screen", ok=True, data=capture))
        else:
            capture = self.screen.capture_active_window(allowed_tools=allowed_tools)
            if capture.get("image_path"):
                tool_results.append(ToolResult("capture_active_window", ok=True, data=capture))
            else:
                capture = self.screen.capture_screen(allowed_tools=allowed_tools)
                tool_results.append(ToolResult("capture_screen", ok=True, data=capture))
        logger.info("screen_capture_done path=%s", capture.get("image_path", ""))

        accessibility = self.screen.get_accessibility_tree(allowed_tools=allowed_tools)
        tool_results.append(ToolResult("get_accessibility_tree", ok=True, data=accessibility))
        logger.info("accessibility_tree_loaded available=%s", accessibility.get("available", False))

        described = self.screen.describe_visible_regions(
            str(capture.get("image_path", "")),
            accessibility,
            allowed_tools=allowed_tools,
            user_goal=user_request,
        )
        tool_results.append(ToolResult("describe_visible_regions", ok=True, data=described))
        logger.info("screen_summary_ready")

        fallback = self._build_local_fallback(active_app, described, use_full_screen)

        screen_summary = str(described.get("screen_summary", "") or "").strip() or fallback["screen_summary"]
        important_regions = self._merge_string_lists(described.get("important_regions"), fallback["important_regions"])
        actionable_elements = self._merge_string_lists(described.get("actionable_elements"), fallback["actionable_elements"])
        suggested_next_step = str(described.get("suggested_next_step", "") or "").strip() or fallback["suggested_next_step"]
        visible_text = self._merge_string_lists(described.get("visible_text"), [])

        response_text = self._compose_response(
            active_app=active_app,
            screen_summary=screen_summary,
            important_regions=important_regions,
            actionable_elements=actionable_elements,
            suggested_next_step=suggested_next_step,
            visible_text=visible_text,
            use_full_screen=use_full_screen,
        )

        return SkillResult(
            skill_name=self.SPEC.name,
            success=True,
            summary=screen_summary,
            structured={
                "current_app": active_app,
                "screen_summary": screen_summary,
                "important_regions": important_regions,
                "actionable_elements": actionable_elements,
                "suggested_next_step": suggested_next_step,
                "visible_text": visible_text,
                "capture_path": capture.get("image_path", ""),
            },
            recommendation=suggested_next_step,
            tool_results=tool_results,
            response_text=response_text,
        )

    def _should_capture_full_screen(self, user_request: str, active_app: dict[str, Any]) -> bool:
        lowered = user_request.lower()
        if any(token in lowered for token in ("\u5c4f\u5e55", "\u684c\u9762", "\u6574\u4e2a\u5c4f\u5e55", "\u5168\u5c4f", "screen")):
            return True
        app_name = str(active_app.get("app_name", "")).lower()
        window_title = str(active_app.get("window_title", "")).lower()
        if app_name in {"python.exe", "pythonw.exe", "nvidia overlay.exe"}:
            return True
        if "fairy" in window_title:
            return True
        return False

    def _build_local_fallback(
        self,
        active_app: dict[str, Any],
        described: dict[str, Any],
        use_full_screen: bool,
    ) -> dict[str, Any]:
        app_name = self._display_app_name(active_app)
        window_title = self._display_window_title(active_app)

        summary_parts: list[str] = []
        if app_name and app_name != "Unknown":
            summary_parts.append(f"\u5f53\u524d\u7126\u70b9\u5e94\u7528\u662f {app_name}\u3002")
        if window_title:
            summary_parts.append(f"\u7a97\u53e3\u6807\u9898\u662f\u201c{window_title}\u201d\u3002")
        if use_full_screen:
            summary_parts.append("\u672c\u6b21\u4f7f\u7528\u6574\u5c4f\u622a\u56fe\uff0c\u907f\u514d\u53ea\u5206\u6790 Fairy \u81ea\u5df1\u7684\u7a97\u53e3\u3002")

        clean_summary = str(described.get("screen_summary", "") or "").strip()
        if clean_summary and not self._looks_like_json_dump(clean_summary):
            summary_parts.append(clean_summary[:240])
        else:
            summary_parts.append("\u622a\u56fe\u5df2\u5b8c\u6210\uff0c\u4f46\u5f53\u524d\u8fd8\u7f3a\u5c11\u8db3\u591f\u660e\u786e\u7684\u754c\u9762\u63cf\u8ff0\u3002")

        suggested_next_step = "\u5982\u679c\u4f60\u8981\u6211\u544a\u8bc9\u4f60\u4e0b\u4e00\u6b65\u70b9\u54ea\u91cc\uff0c\u8bf7\u628a\u76ee\u6807\u754c\u9762\u4fdd\u6301\u5728\u524d\u53f0\u540e\u518d\u91cd\u65b0\u5206\u6790\u3002"
        return {
            "screen_summary": " ".join(summary_parts).strip(),
            "important_regions": [],
            "actionable_elements": [],
            "suggested_next_step": suggested_next_step,
        }

    def _merge_string_lists(self, primary: Any, fallback: list[str]) -> list[str]:
        items: list[str] = []
        if isinstance(primary, list):
            items.extend(str(item).strip() for item in primary if str(item).strip())
        elif isinstance(primary, str) and primary.strip() and not self._looks_like_json_dump(primary):
            items.append(primary.strip())
        if items:
            return items
        return [item for item in fallback if item]

    def _compose_response(
        self,
        *,
        active_app: dict[str, Any],
        screen_summary: str,
        important_regions: list[str],
        actionable_elements: list[str],
        suggested_next_step: str,
        visible_text: list[str],
        use_full_screen: bool,
    ) -> str:
        app_name = self._display_app_name(active_app)
        lines = [
            "\u8bf7\u6c42\u5df2\u63a5\u6536\u3002",
            f"\u5f53\u524d\u8bc6\u522b\u5230\u7684\u76ee\u6807\u5e94\u7528\u662f {app_name}\u3002",
        ]
        if use_full_screen:
            lines.append("\u672c\u6b21\u6309\u6574\u5c4f\u8fdb\u884c\u5206\u6790\uff0c\u4e0d\u53ea\u770b Fairy \u5f53\u524d\u7a97\u53e3\u3002")
        lines.append(screen_summary or "\u5f53\u524d\u754c\u9762\u5df2\u8bfb\u53d6\uff0c\u4f46\u6458\u8981\u4fe1\u606f\u4e0d\u8db3\u3002")

        detail_lines: list[str] = []
        if important_regions:
            detail_lines.append("\u91cd\u70b9\u533a\u57df\uff1a" + "\u3001".join(important_regions[:3]) + "\u3002")
        if actionable_elements:
            detail_lines.append("\u53ef\u64cd\u4f5c\u5143\u7d20\uff1a" + "\u3001".join(actionable_elements[:3]) + "\u3002")
        if visible_text:
            detail_lines.append("\u663e\u8457\u6587\u672c\uff1a" + "\u3001".join(visible_text[:3]) + "\u3002")
        if suggested_next_step:
            detail_lines.append("\u5efa\u8bae\uff1a" + suggested_next_step)

        return "\n".join(lines + detail_lines).strip()

    def _display_app_name(self, active_app: dict[str, Any]) -> str:
        context_app = active_app.get("context_app")
        if isinstance(context_app, dict):
            name = str(context_app.get("app_name", "") or "").strip()
            if name and name.lower() not in {"python.exe", "pythonw.exe", "textinputhost.exe", "nvidia overlay.exe"}:
                return name
        return str(active_app.get("app_name", "") or "Unknown")

    def _display_window_title(self, active_app: dict[str, Any]) -> str:
        context_app = active_app.get("context_app")
        if isinstance(context_app, dict):
            title = str(context_app.get("window_title", "") or "").strip()
            if title:
                return title
        return str(active_app.get("window_title", "") or "").strip()

    def _looks_like_json_dump(self, text: str) -> bool:
        lowered = text.lower().strip()
        return lowered.startswith("{") or lowered.startswith("```json") or '"screen_summary"' in lowered
