from datetime import datetime
from pathlib import Path

from app.lazy_runtime.context_builder import FAIRY_LAZY_CORE_IDENTITY
from app.persona.persona_engine import PersonaEngine
from app.persona.tone_preference import build_fairy_tone_preference_result, looks_like_fairy_tone_preference
from app.prompts import FAIRY_CORE_SYSTEM_PROMPT, build_core_system_prompt


def test_core_prompt_uses_system_ai_tone_not_partner_intro() -> None:
    prompt = build_core_system_prompt(now=datetime(2026, 5, 16, 12, 0, 0), active_mode="normal_mode")

    assert "系统级智能体" in prompt
    assert "任务优先" in prompt
    assert "不要说“我是你的技术合伙人”" in prompt
    assert "你的角色更像用户的技术合伙人" not in prompt
    assert "AI 合伙人" not in FAIRY_CORE_SYSTEM_PROMPT


def test_runtime_direct_answer_prompt_blocks_technical_partner_intro() -> None:
    source = Path("app/api/dependencies.py").read_text(encoding="utf-8")

    assert "resident desktop system AI" in source
    assert "Do not call yourself a technical partner" in source
    assert "desktop AI partner" not in source


def test_lightweight_persona_uses_resident_system_ai_tone() -> None:
    prompt = PersonaEngine().build_lightweight_prompt("chat")

    assert "系统级智能体" in prompt
    assert "不要自称技术合伙人" in prompt
    assert "个人 AI 助手与技术合伙人" not in prompt


def test_lazy_runtime_identity_uses_same_fairy_tone() -> None:
    assert "系统级智能体" in FAIRY_LAZY_CORE_IDENTITY
    assert "在线。任务目标？" in FAIRY_LAZY_CORE_IDENTITY
    assert "你的角色是用户的技术合伙人" not in FAIRY_LAZY_CORE_IDENTITY
    assert "个人 AI 助手" not in FAIRY_LAZY_CORE_IDENTITY


def test_fairy_tone_preference_short_circuits_old_partner_reply() -> None:
    assert looks_like_fairy_tone_preference("我想要的是绝区零里Fairy的性格和语气")
    assert not looks_like_fairy_tone_preference("绝区零 Fairy 是谁")

    result = build_fairy_tone_preference_result(
        task_id="task-1",
        request_origin="api_local",
        request_id="req-1",
    )

    assert result.skill_name == "persona_preference"
    assert "确认：语气偏好已切换" in result.response_text
    assert "不再使用“技术合伙人”式开场" in result.response_text
