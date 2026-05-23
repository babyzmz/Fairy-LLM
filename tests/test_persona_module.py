from __future__ import annotations

import unittest

from app.persona.persona_engine import PersonaEngine


class PersonaModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PersonaEngine()

    def test_persona_prompt_contains_structured_sections(self) -> None:
        prompt = self.engine.build_persona_prompt(
            context={"chosen_skill": "terminal-agent", "memory_category": "coding_help"},
            user_profile=[{"key": "pref_direct", "value": "回答更直接，少废话"}],
            task_type="coding",
            recent_summary="最近在重构 Fairy 的工具链和记忆注入。",
        )
        self.assertIn("[Persona core]", prompt)
        self.assertIn("[Style profile]", prompt)
        self.assertIn("[Relationship overlay]", prompt)
        self.assertIn("[Task behavior]", prompt)
        self.assertIn("[Anti-drift guard]", prompt)

    def test_style_examples_follow_fairy_normal(self) -> None:
        cases = [
            (
                "我现在有点乱，不知道先改记忆还是先做联网搜索",
                "先做联网搜索。原因很简单，外部信息核实能力是主链路能力，优先级高于人格细化与记忆打磨。记忆可以后补，但搜索缺失会直接削弱 Fairy 的实用性。",
                "planning",
                "判断：",
            ),
            (
                "这个方案是不是太复杂了",
                "当前设计已经开始变复杂了。但还没失控。先收缩边界，保留核心链路，再决定是否扩展。",
                "planning",
                "肯定",
            ),
            (
                "你觉得我是不是又想太多了",
                "你确实有一点过度展开。不是方向错了，是分支太多。先锁一个主目标，不要同时优化所有层。",
                "chat",
                "确认",
            ),
            (
                "夸夸我",
                "可以。你推进项目的能力不差，至少你知道自己要的不是玩具助手，而是真正能协作的系统。这个判断，我认可。",
                "chat",
                "判断：",
            ),
        ]
        expected_openers = {"planning": ("判断：", "结论：", "肯定"), "chat": ("判断：", "确认", "可以。判断：")}
        for user_input, raw_text, task_type, required in cases:
            styled, report = self.engine.style_response(raw_text, task_type=task_type, user_input=user_input)
            self.assertTrue(any(styled.startswith(prefix) or prefix in styled[:30] for prefix in expected_openers[task_type]))
            self.assertIn(required, styled)
            self.assertFalse(report.cute_score)
            self.assertNotRegex(styled, r"好哒|宝宝|亲亲|人家觉得")


if __name__ == "__main__":
    unittest.main()
