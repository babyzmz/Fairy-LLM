from __future__ import annotations

import ctypes
import json
import re
from ctypes import wintypes
from pathlib import Path
from typing import Any, Iterable

from PIL import ImageGrab

from app.ai.llm_client import LLMClient
from app.config import agent_config
from app.mcp_client_layer import MCPClientLayer
from app.tool_registry import ToolRegistry


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
SYSTEM_OVERLAY_PROCESSES = {
    "python.exe",
    "pythonw.exe",
    "textinputhost.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "nvidia overlay.exe",
}


class ScreenCapability:
    def __init__(self, registry: ToolRegistry, mcp: MCPClientLayer, llm: LLMClient) -> None:
        self.registry = registry
        self.mcp = mcp
        self.llm = llm
        self.capture_dir = agent_config.screenshot_dir
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self._register_tools()

    def _register_tools(self) -> None:
        self.registry.register("capture_screen", "Capture the current screen.", self._tool_capture_screen)
        self.registry.register("capture_active_window", "Capture the current foreground window.", self._tool_capture_active_window)
        self.registry.register("get_active_app", "Get foreground app metadata.", self._tool_get_active_app)
        self.registry.register(
            "get_accessibility_tree",
            "Get accessibility tree if available; otherwise return fallback metadata.",
            self._tool_get_accessibility_tree,
        )
        self.registry.register(
            "describe_visible_regions",
            "Describe a screenshot with UI understanding and next-step advice.",
            self._tool_describe_visible_regions,
        )

    def capture_screen(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("capture_screen", allowed_tools=allowed_tools).data

    def capture_active_window(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("capture_active_window", allowed_tools=allowed_tools).data

    def get_active_app(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("get_active_app", allowed_tools=allowed_tools).data

    def get_accessibility_tree(self, *, allowed_tools: Iterable[str]) -> dict[str, Any]:
        return self.mcp.call_tool("get_accessibility_tree", allowed_tools=allowed_tools).data

    def describe_visible_regions(
        self,
        image_path: str,
        accessibility_tree: dict[str, Any] | None,
        *,
        allowed_tools: Iterable[str],
        user_goal: str = "",
    ) -> dict[str, Any]:
        return self.mcp.call_tool(
            "describe_visible_regions",
            allowed_tools=allowed_tools,
            image_path=image_path,
            accessibility_tree=accessibility_tree or {},
            user_goal=user_goal,
        ).data

    def _tool_capture_screen(self) -> dict[str, Any]:
        image = ImageGrab.grab(all_screens=True)
        path = self.capture_dir / "screen_capture.png"
        image.save(path)
        return {"image_path": str(path), "size": image.size}

    def _tool_capture_active_window(self) -> dict[str, Any]:
        info = self._foreground_window_info()
        bbox = info.get("bbox")
        if not bbox:
            return self._tool_capture_screen()
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        path = self.capture_dir / "active_window_capture.png"
        image.save(path)
        return {"image_path": str(path), "size": image.size, "window": info}

    def _tool_get_active_app(self) -> dict[str, Any]:
        info = self._foreground_window_info()
        context_app = self._guess_context_app(info)
        if context_app:
            info["context_app"] = context_app
        return info

    def _tool_get_accessibility_tree(self) -> dict[str, Any]:
        return {
            "available": False,
            "reason": "Accessibility tree is not wired on this Windows build yet.",
            "elements": [],
        }

    def _tool_describe_visible_regions(
        self,
        image_path: str,
        accessibility_tree: dict[str, Any] | None = None,
        user_goal: str = "",
    ) -> dict[str, Any]:
        acc_text = str(accessibility_tree or {})
        response = self.llm.execute_task(
            (
                "You are a UI understanding module. "
                "Read the screenshot carefully and answer in Chinese. "
                "Prefer this exact labeled format without markdown fences:\n"
                "SCREEN_SUMMARY: ...\n"
                "IMPORTANT_REGIONS:\n- ...\n- ...\n"
                "ACTIONABLE_ELEMENTS:\n- ...\n- ...\n"
                "SUGGESTED_NEXT_STEP: ...\n"
                "VISIBLE_TEXT:\n- ...\n- ...\n"
                "If you can return valid JSON with keys screen_summary, important_regions, actionable_elements, suggested_next_step, visible_text, that is also acceptable. "
                "Do not emit explanations outside these sections. "
                "Focus on what is actually visible on the screen and the user's goal."
            ),
            f"\u7528\u6237\u76ee\u6807\uff1a{user_goal or '未提供'}\n\u8f85\u52a9\u53ef\u8bbf\u95ee\u6027\u4fe1\u606f\uff1a{acc_text}",
            attachment_paths=[image_path],
            max_tokens=640,
            temperature=0.1,
        )
        parsed = self._parse_structured_output(response.text)
        if parsed is None:
            raw_text = self._clean_model_text(response.text)
            if self._looks_like_json_dump(raw_text):
                summary_text = "\u622a\u56fe\u5df2\u8bfb\u53d6\uff0c\u4f46\u8fd9\u6b21\u754c\u9762\u7ed3\u6784\u89e3\u6790\u4e0d\u591f\u7a33\u5b9a\u3002"
            else:
                summary_text = raw_text[:400] if raw_text else "\u622a\u56fe\u5df2\u8bfb\u53d6\uff0c\u4f46\u672a\u80fd\u751f\u6210\u53ef\u7528\u7684\u754c\u9762\u6458\u8981\u3002"
            return {
                "image_path": image_path,
                "raw_text": raw_text,
                "screen_summary": summary_text,
                "important_regions": [],
                "actionable_elements": [],
                "suggested_next_step": "\u8bf7\u628a\u76ee\u6807\u754c\u9762\u4fdd\u6301\u5728\u524d\u53f0\uff0c\u6211\u518d\u7ee7\u7eed\u5206\u6790\u3002",
                "visible_text": [],
            }

        return {
            "image_path": image_path,
            "raw_text": self._clean_model_text(response.text),
            "screen_summary": str(parsed.get("screen_summary", "") or "").strip(),
            "important_regions": self._normalize_string_list(parsed.get("important_regions")),
            "actionable_elements": self._normalize_string_list(parsed.get("actionable_elements")),
            "suggested_next_step": str(parsed.get("suggested_next_step", "") or "").strip(),
            "visible_text": self._normalize_string_list(parsed.get("visible_text")),
        }

    def _parse_structured_output(self, text: str) -> dict[str, Any] | None:
        parsed = self._parse_json_object(text)
        if parsed is not None:
            return parsed
        return self._parse_labeled_sections(text)

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        text = text.strip()
        if not text:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        for candidate in (cleaned, text):
            try:
                data = json.loads(candidate)
                return data if isinstance(data, dict) else None
            except Exception:
                pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _parse_labeled_sections(self, text: str) -> dict[str, Any] | None:
        cleaned = self._clean_model_text(text)
        if not cleaned:
            return None
        label_map = {
            "SCREEN_SUMMARY": "screen_summary",
            "IMPORTANT_REGIONS": "important_regions",
            "ACTIONABLE_ELEMENTS": "actionable_elements",
            "SUGGESTED_NEXT_STEP": "suggested_next_step",
            "VISIBLE_TEXT": "visible_text",
        }
        result: dict[str, Any] = {
            "screen_summary": "",
            "important_regions": [],
            "actionable_elements": [],
            "suggested_next_step": "",
            "visible_text": [],
        }
        current_key: str | None = None
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            matched_label = None
            for label, key in label_map.items():
                prefix = f"{label}:"
                if line.upper().startswith(prefix):
                    matched_label = key
                    current_key = key
                    value = line[len(prefix) :].strip()
                    if key in {"important_regions", "actionable_elements", "visible_text"}:
                        if value:
                            result[key].append(value.lstrip("- ").strip())
                    else:
                        result[key] = value
                    break
            if matched_label:
                continue
            if current_key in {"important_regions", "actionable_elements", "visible_text"}:
                result[current_key].append(line.lstrip("- ").strip())
                continue
            if current_key in {"screen_summary", "suggested_next_step"}:
                existing = str(result[current_key]).strip()
                result[current_key] = f"{existing} {line}".strip() if existing else line
        if any(result.values()):
            return result
        return None

    def _looks_like_json_dump(self, text: str) -> bool:
        lowered = text.lower()
        return "```json" in lowered or '"screen_summary"' in lowered or lowered.startswith("{")

    def _clean_model_text(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def _normalize_string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        return []

    def _foreground_window_info(self) -> dict[str, Any]:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"app_name": "Unknown", "window_title": "", "bbox": None}
        return self._window_info_from_hwnd(hwnd)

    def _window_info_from_hwnd(self, hwnd: int) -> dict[str, Any]:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        title = title_buffer.value

        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        bbox = (rect.left, rect.top, rect.right, rect.bottom)

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = int(pid.value)

        process_name = ""
        exe_path = ""
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(1024)
                if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    exe_path = buffer.value
                    process_name = Path(exe_path).name
            finally:
                kernel32.CloseHandle(handle)

        return {
            "app_name": process_name or "Unknown",
            "window_title": title,
            "exe_path": exe_path,
            "process_id": process_id,
            "bbox": bbox,
        }

    def _guess_context_app(self, foreground_info: dict[str, Any]) -> dict[str, Any] | None:
        foreground_name = str(foreground_info.get("app_name", "") or "").lower()
        foreground_title = str(foreground_info.get("window_title", "") or "").lower()
        if foreground_name not in SYSTEM_OVERLAY_PROCESSES and "fairy" not in foreground_title:
            return None

        user32 = ctypes.windll.user32
        candidates: list[dict[str, Any]] = []

        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            info = self._window_info_from_hwnd(hwnd)
            app_name = str(info.get("app_name", "") or "").lower()
            title = str(info.get("window_title", "") or "").strip()
            bbox = info.get("bbox")
            if not title or not bbox:
                return True
            if app_name in SYSTEM_OVERLAY_PROCESSES:
                return True
            width = max(0, int(bbox[2]) - int(bbox[0]))
            height = max(0, int(bbox[3]) - int(bbox[1]))
            area = width * height
            if area < 120000:
                return True
            info["area"] = area
            candidates.append(info)
            return True

        user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
        if not candidates:
            return None
        candidates.sort(key=lambda item: int(item.get("area", 0)), reverse=True)
        best = dict(candidates[0])
        best.pop("area", None)
        return best
