from __future__ import annotations

from app.settings.game_mode_settings import GameModeSettings


def build_game_mode_prompt_overlay(settings: GameModeSettings) -> str:
    lines = [
        "[Game mode overlay]",
        "当前处于游戏模式。",
        "回复更短、更直接、结论优先。",
        "默认减少非必要解释与打断。",
    ]
    brevity_map = {
        "ultra_short": "尽量控制在 1 到 2 句。",
        "short": "尽量控制在 2 到 3 句。",
        "balanced": "在不影响判断的前提下保持简洁。",
    }
    verdict_map = {
        "soft": "适度使用判断词。",
        "strong": "提高结论感，优先给判断和建议。",
        "hard": "明显强化 verdict 风格，但不要做作。",
    }
    interruption_map = {
        "low_interrupt": "避免主动扩展无关建议。",
        "balanced": "只在确有必要时补充建议。",
        "assistive": "在不干扰的前提下给出下一步建议。",
    }
    lines.append(brevity_map.get(settings.brevity_level, brevity_map["short"]))
    lines.append(verdict_map.get(settings.verdict_strength, verdict_map["strong"]))
    lines.append(interruption_map.get(settings.interruption_policy, interruption_map["low_interrupt"]))
    if settings.system_prompt_overlay.strip():
        lines.append(settings.system_prompt_overlay.strip())
    return "\n".join(lines)
