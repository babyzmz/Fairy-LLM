from __future__ import annotations

import re

from app.core.perception.perception_models import PerceptionIntent


class IntentClassifier:
    _SYSTEM_ACTION_PATTERNS = (
        "refresh capabilities",
        "reload capabilities",
        "clear cache",
        "clear asset cache",
        "restart backend",
        "restart service",
        "open system panel",
        "open debug panel",
        "focus window",
        "show main window",
        "show notification",
        "reveal asset folder",
        "\u5237\u65b0\u80fd\u529b",
        "\u5237\u65b0\u529f\u80fd\u5217\u8868",
        "\u91cd\u65b0\u52a0\u8f7d\u80fd\u529b",
        "\u6e05\u7406\u7f13\u5b58",
        "\u6e05\u9664\u7f13\u5b58",
        "\u6e05\u7406\u8d44\u6e90\u7f13\u5b58",
        "\u6e05\u9664\u8d44\u6e90\u7f13\u5b58",
        "\u91cd\u542f\u540e\u7aef",
        "\u91cd\u542f\u670d\u52a1",
        "\u6253\u5f00\u7cfb\u7edf\u9762\u677f",
        "\u6253\u5f00\u8c03\u8bd5\u9762\u677f",
        "\u805a\u7126\u7a97\u53e3",
        "\u5207\u5230\u4e3b\u7a97\u53e3",
        "\u663e\u793a\u901a\u77e5",
        "\u5f39\u51fa\u901a\u77e5",
        "\u6253\u5f00\u7f13\u5b58\u76ee\u5f55",
        "\u6253\u5f00\u8d44\u6e90\u76ee\u5f55",
    )
    _CONTROL_PATTERNS = (
        "click",
        "close",
        "scroll",
        "type",
        "drag",
        "open app",
        "\u70b9\u51fb",
        "\u5173\u95ed",
        "\u6eda\u52a8",
        "\u8f93\u5165",
        "\u62d6\u52a8",
        "\u6253\u5f00\u8f6f\u4ef6",
        "\u6253\u5f00\u5e94\u7528",
    )
    _RESEARCH_PATTERNS = (
        "news",
        "latest",
        "research",
        "compare",
        "\u8c03\u67e5",
        "\u7814\u7a76",
        "\u6bd4\u8f83",
        "\u65b0\u95fb",
        "\u6700\u65b0",
    )
    _GENERIC_SEARCH_PATTERNS = (
        "search",
        "find",
        "look up",
        "check",
        "\u67e5",
        "\u641c",
        "\u5e2e\u6211\u67e5",
        "\u5e2e\u6211\u627e",
        "\u770b\u770b",
        "\u6709\u6ca1\u6709",
        "\u54ea\u4e2a\u597d",
        "\u7ed9\u6211\u67e5\u4e00\u4e0b",
    )
    _LOOKUP_PATTERNS = (
        "weather",
        "where is",
        "address",
        "location",
        "map",
        "stock",
        "price",
        "time",
        "forecast",
        "\u5929\u6c14",
        "\u5730\u56fe",
        "\u5730\u5740",
        "\u4f4d\u7f6e",
        "\u9644\u8fd1",
        "\u6700\u8fd1",
        "\u5750\u6807",
        "\u4ef7\u683c",
        "\u6c47\u7387",
        "\u65f6\u95f4",
        "\u5728\u54ea",
        "\u5728\u54ea\u91cc",
    )
    _EXPLANATION_PATTERNS = (
        "explain",
        "why",
        "how does",
        "what is the difference",
        "\u89e3\u91ca",
        "\u4e3a\u4ec0\u4e48",
        "\u600e\u4e48",
        "\u539f\u7406",
        "\u533a\u522b",
        "\u5982\u4f55\u7406\u89e3",
        "\u4ec0\u4e48\u662f",
        "\u662f\u4ec0\u4e48",
        "\u4ec0\u4e48\u610f\u601d",
        "\u662f\u4ec0\u4e48\u610f\u601d",
        "\u89e3\u91ca\u4e00\u4e0b",
        "\u600e\u4e48\u56de\u4e8b",
        "\u4e3a\u4ec0\u4e48\u4f1a\u8fd9\u6837",
    )
    _SURFACE_TOKENS = (
        "screen",
        "window",
        "button",
        "menu",
        "desktop",
        "\u754c\u9762",
        "\u7a97\u53e3",
        "\u6309\u94ae",
        "\u83dc\u5355",
        "\u5c4f\u5e55",
        "\u684c\u9762",
    )

    def classify(self, normalized_text: str) -> tuple[PerceptionIntent, float]:
        lowered = (normalized_text or "").lower()
        if self._contains_any(lowered, self._SYSTEM_ACTION_PATTERNS):
            return "system_action", 0.95
        if self._contains_any(lowered, self._CONTROL_PATTERNS) and self._mentions_surface(lowered):
            return "control", 0.94
        if self._contains_any(lowered, self._RESEARCH_PATTERNS):
            return "research", 0.9
        if self._contains_any(lowered, self._LOOKUP_PATTERNS):
            return "lookup", 0.92
        if self._contains_any(lowered, self._EXPLANATION_PATTERNS) or len(re.findall(r"\bwhy\b|\bhow\b", lowered)) > 0:
            return "explanation", 0.86
        if self._contains_any(lowered, self._GENERIC_SEARCH_PATTERNS):
            return "factual", 0.78
        return "factual", 0.66

    def _mentions_surface(self, lowered: str) -> bool:
        return any(token in lowered for token in self._SURFACE_TOKENS)

    @staticmethod
    def _contains_any(lowered: str, patterns: tuple[str, ...]) -> bool:
        return any(pattern in lowered for pattern in patterns)
