from __future__ import annotations

from app.models.skill_result import SkillResult


FAIRY_TONE_SOURCE_MARKERS = ("fairy", "绝区零", "zenless", "zzz")
FAIRY_TONE_REQUEST_MARKERS = ("性格", "语气", "口吻", "风格", "人设", "说话方式", "回复样式")
FAIRY_TONE_PREFERENCE_MARKERS = ("我想要", "想要的是", "改成", "换成", "参考", "像", "不是", "别再", "不要再")


def looks_like_fairy_tone_preference(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    has_source = any(marker in lowered for marker in FAIRY_TONE_SOURCE_MARKERS)
    has_tone_target = any(marker in raw for marker in FAIRY_TONE_REQUEST_MARKERS)
    has_preference = any(marker in raw for marker in FAIRY_TONE_PREFERENCE_MARKERS)
    return has_source and has_tone_target and has_preference


def build_fairy_tone_preference_result(*, task_id: str, request_origin: str, request_id: str) -> SkillResult:
    text = (
        "确认：语气偏好已切换。后续按 Fairy 的系统 AI 方向输出：冷静、精确、任务优先，"
        "带一点判断感和低频吐槽；不再使用“技术合伙人”式开场，也不复述原作台词。"
    )
    return SkillResult(
        skill_name="persona_preference",
        success=True,
        summary="Fairy 语气偏好已更新为系统 AI 风格。",
        response_text=text,
        structured={
            "task_id": task_id,
            "intent": "persona_preference",
            "persona_tone": "fairy_system_ai",
            "request_origin": request_origin,
            "request_id": request_id,
        },
    )
