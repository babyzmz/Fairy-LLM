from __future__ import annotations

from app.runtime.system_actions.action_models import SystemActionResolution


class SystemActionRegistry:
    def resolve(self, message: str) -> SystemActionResolution:
        normalized = " ".join(str(message or "").strip().lower().split())

        direct_match = self._resolve_direct_action_name(normalized)
        if direct_match is not None:
            return direct_match

        if self._contains_any(
            normalized,
            (
                "cancel current request",
                "stop current request",
                "取消当前请求",
                "取消当前任务",
                "停止当前请求",
                "停止当前回答",
                "中断当前回答",
            ),
        ):
            return SystemActionResolution(
                name="cancel_current_request",
                category="backend_action",
                summary="Cancel the active request.",
                reason="matched_cancel_current_request",
            )

        if self._contains_any(
            normalized,
            (
                "clear asset cache",
                "clear cache",
                "清理缓存",
                "清除缓存",
                "清理资源缓存",
                "清除资源缓存",
            ),
        ):
            return SystemActionResolution(
                name="clear_asset_cache",
                category="backend_action",
                summary="Clear cached asset files.",
                reason="matched_clear_asset_cache",
            )

        if self._contains_any(
            normalized,
            (
                "refresh capabilities",
                "reload capabilities",
                "刷新能力",
                "刷新功能列表",
                "重新加载能力",
            ),
        ):
            return SystemActionResolution(
                name="refresh_capabilities",
                category="backend_action",
                summary="Refresh runtime capability metadata.",
                reason="matched_refresh_capabilities",
            )

        if self._contains_any(
            normalized,
            (
                "restart backend",
                "restart service",
                "重启后端",
                "重启服务",
            ),
        ):
            return SystemActionResolution(
                name="restart_backend",
                category="desktop_action",
                summary="Restart the local Fairy backend process.",
                reason="matched_restart_backend",
            )

        if self._contains_any(
            normalized,
            (
                "reveal asset folder",
                "open asset folder",
                "打开缓存目录",
                "打开资源目录",
                "打开资源缓存目录",
            ),
        ):
            return SystemActionResolution(
                name="reveal_asset_folder",
                category="desktop_action",
                summary="Reveal the local asset cache folder.",
                reason="matched_reveal_asset_folder",
            )

        if self._contains_any(
            normalized,
            (
                "open system panel",
                "open debug panel",
                "打开系统面板",
                "打开调试面板",
            ),
        ):
            panel = "debug" if "debug" in normalized or "调试" in normalized else "system"
            return SystemActionResolution(
                name="open_panel",
                category="desktop_action",
                summary="Open a desktop panel in the shell.",
                payload={"panel": panel},
                reason="matched_open_panel",
            )

        if self._contains_any(
            normalized,
            (
                "focus window",
                "show main window",
                "聚焦窗口",
                "切到主窗口",
                "显示主窗口",
            ),
        ):
            return SystemActionResolution(
                name="focus_window",
                category="desktop_action",
                summary="Focus the main desktop window.",
                reason="matched_focus_window",
            )

        if self._contains_any(
            normalized,
            (
                "show notification",
                "显示通知",
                "弹出通知",
            ),
        ):
            return SystemActionResolution(
                name="show_notification",
                category="desktop_action",
                summary="Show a desktop notification.",
                payload={"message": self._extract_notification_message(message)},
                reason="matched_show_notification",
            )

        if self._contains_any(
            normalized,
            (
                "点击",
                "点一下",
                "滚动",
                "输入",
                "拖动",
                "右上角",
                "左下角",
                "屏幕",
                "这个窗口",
                "click",
                "scroll",
                "type ",
                "drag",
                "cursor",
                "mouse",
            ),
        ):
            return SystemActionResolution(
                name="desktop_automation_compatibility_action",
                category="desktop_automation_compatibility",
                summary="This request targets the desktop automation compatibility path.",
                reason="matched_desktop_automation_compatibility",
            )

        return SystemActionResolution(
            name="unknown_system_action",
            category="unknown",
            summary="No supported system action matched the request.",
            reason="no_system_action_match",
        )

    def _resolve_direct_action_name(self, normalized: str) -> SystemActionResolution | None:
        mapping: dict[str, tuple[str, str]] = {
            "cancel_current_request": ("backend_action", "Cancel the active request."),
            "clear_asset_cache": ("backend_action", "Clear cached asset files."),
            "refresh_capabilities": ("backend_action", "Refresh runtime capability metadata."),
            "restart_backend": ("desktop_action", "Restart the local Fairy backend process."),
            "reveal_asset_folder": ("desktop_action", "Reveal the local asset cache folder."),
            "open_panel": ("desktop_action", "Open a desktop panel in the shell."),
            "focus_window": ("desktop_action", "Focus the main desktop window."),
            "show_notification": ("desktop_action", "Show a desktop notification."),
            "desktop_automation_compatibility_action": (
                "desktop_automation_compatibility",
                "This request targets the desktop automation compatibility path.",
            ),
        }
        matched = mapping.get(normalized)
        if matched is None:
            return None
        category, summary = matched
        payload = {"panel": "system"} if normalized == "open_panel" else None
        if normalized == "show_notification":
            payload = {"message": "Fairy notification"}
        return SystemActionResolution(
            name=normalized,
            category=category,  # type: ignore[arg-type]
            summary=summary,
            payload=payload,
            reason="matched_direct_action_name",
        )

    def supported_backend_actions(self) -> list[str]:
        return ["cancel_current_request", "clear_asset_cache", "refresh_capabilities"]

    def supported_desktop_actions(self) -> list[str]:
        return ["restart_backend", "reveal_asset_folder", "open_panel", "focus_window", "show_notification"]

    @staticmethod
    def _contains_any(normalized: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in normalized for phrase in phrases)

    @staticmethod
    def _extract_notification_message(message: str) -> str:
        raw = str(message or "").strip()
        if not raw:
            return "Fairy notification"
        for marker in ("显示通知", "弹出通知", "show notification"):
            raw = raw.replace(marker, "")
        cleaned = raw.strip(" ：:，,。.!")
        return cleaned or "Fairy notification"
