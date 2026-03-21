from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SessionRollupDecision:
    should_rollup: bool
    reason: str = ""


class SessionAggregator:
    def should_rollup_session_summary(
        self,
        *,
        session_message_count: int,
        existing_summary_count: int,
        score: int,
        turn_threshold: int,
        threshold_summary: int,
        max_summaries: int,
        session_rollup_enabled: bool,
    ) -> SessionRollupDecision:
        if not session_rollup_enabled:
            return SessionRollupDecision(False, "disabled")
        if existing_summary_count >= max_summaries:
            return SessionRollupDecision(False, "summary_cap_reached")
        if score >= threshold_summary and session_message_count >= turn_threshold:
            return SessionRollupDecision(True, "turn_threshold_and_value")
        if score >= threshold_summary + 1:
            return SessionRollupDecision(True, "high_value_rollup")
        return SessionRollupDecision(False, "low_value_or_too_early")

    def build_session_rollup(
        self,
        *,
        session_title: str,
        user_request: str,
        result_summary: str,
        recommendation: str,
        recent_messages: list[dict],
    ) -> str:
        completed = result_summary.strip() or "本阶段尚未形成明确结果。"
        latest_user = next(
            (str(item.get("content", "")).strip() for item in recent_messages if str(item.get("role", "")) == "user"),
            user_request.strip(),
        )
        latest_assistant = next(
            (str(item.get("content", "")).strip() for item in recent_messages if str(item.get("role", "")) == "assistant"),
            completed,
        )
        parts = [
            f"阶段主题：{session_title or user_request.strip()}",
            f"完成事项：{completed}",
            f"关键判断：{latest_assistant[:260]}",
        ]
        if recommendation.strip():
            parts.append(f"待解决项：{recommendation.strip()}")
        else:
            parts.append(f"待解决项：围绕“{latest_user[:120]}”继续推进。")
        return "\n".join(parts)[:1200]
