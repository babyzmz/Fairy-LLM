from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.models.skill_result import SkillRoute
from app.skills.skill_spec import SkillSpec


logger = logging.getLogger(__name__)


WEB_HINTS = (
    "上网",
    "联网",
    "搜索",
    "网页",
    "网站",
    "官网",
    "浏览",
    "链接",
    "url",
    "参数",
    "价格",
    "版本",
    "商品",
    "文档网站",
    "b站",
    "哔哩哔哩",
)

REALTIME_HINTS = (
    "最新",
    "最近",
    "实时",
    "今日",
    "今天",
    "刚刚",
    "现在",
)

WEATHER_HINTS = (
    "天气",
    "气温",
    "温度",
    "降雨",
    "下雨",
    "湿度",
    "刮风",
    "weather",
    "forecast",
    "预报",
)

NEWS_HINTS = (
    "新闻",
    "头条",
    "快讯",
    "动态",
    "news",
)

NEWS_INTELLIGENCE_HINTS = (
    "it之家",
    "ithome",
    "科技简报",
    "值得看的科技新闻",
    "技术观察库",
    "观察库",
    "项目相关的新闻",
    "fairy项目相关",
    "fairy 项目相关",
    "和 fairy 项目相关",
)

KNOWLEDGE_HINTS = (
    "我们之前",
    "之前",
    "以前",
    "先前",
    "上次",
    "讨论过",
    "决定了什么",
    "做到哪了",
    "怎么定的",
    "怎么说来着",
    "历史记录",
    "历史里",
    "从历史记录",
    "session summary",
    "decision card",
)

SYSTEM_HINTS = (
    "待处理通知",
    "通知",
    "提醒",
    "jobs",
    "job",
    "reindex",
    "索引",
    "active fingerprint",
    "fingerprint",
    "backend",
    "向量库",
    "collection",
    "provider",
    "model",
    "运行中的任务",
    "pending decisions",
    "待确认决定",
)

DOCUMENT_HINTS = (
    "文件",
    "文档",
    "markdown",
    "md",
    "txt",
    "json",
    "csv",
    "yaml",
    "yml",
    ".py",
    "目录",
    "读取",
    "总结",
    "提取重点",
    "修改文档",
    "保存为新文件",
    "diff",
    "patch",
)

SCREEN_HINTS = (
    "屏幕",
    "窗口",
    "界面",
    "截图",
    "点哪里",
    "下一步",
    "看看我屏幕",
    "活动窗口",
    "任务栏",
    "小地图",
    "技能栏",
    "当前画面",
)

SCREEN_FOLLOWUP_HINTS = (
    "右侧",
    "左侧",
    "上面",
    "下面",
    "这里",
    "那里",
    "这个",
    "那个",
    "任务",
    "去哪",
    "怎么走",
    "该点",
    "该做什么",
    "哪一个",
    "按钮",
    "然后呢",
    "接下来",
)

AGENT_HINTS = (
    "代码",
    "仓库",
    "repo",
    "project",
    "终端",
    "命令",
    "shell",
    "测试",
    "pytest",
    "build",
    "lint",
    "修复",
    "实现",
    "重构",
    "改代码",
    "apply patch",
    "read file",
    "run command",
)

LOCATION_HINTS = (
    "where is",
    "where's",
    "在哪",
    "在哪里",
    "哪儿",
    "地图",
    "地图预览",
    "地图展示",
    "地图显示",
    "位置",
    "地址",
    "地点",
    "坐标",
    "附近",
    "最近",
    "nearby",
    "nearest",
    "post office",
)

MAP_DISPLAY_HINTS = (
    "显示地图",
    "给我看看地图",
    "把地图打开看看",
    "地图展示一下",
    "地图显示一下",
    "地图显示出来",
    "地图打开看看",
    "看看地图",
    "地图呢",
    "show map",
    "show me the map",
    "display the map",
    "open the map",
)

UI_CONTROL_VERBS = (
    "点击",
    "点开",
    "关闭",
    "切换",
    "滚动",
    "输入",
    "拖动",
    "选中",
    "最小化",
    "最大化",
    "操作",
    "控制",
    "click",
    "open app",
    "open the app",
    "close",
    "switch",
    "scroll",
    "type",
    "press",
    "launch",
    "control",
)

SCREEN_ELEMENT_HINTS = (
    "屏幕",
    "界面",
    "窗口",
    "按钮",
    "任务栏",
    "菜单",
    "面板",
    "对话框",
    "标签页",
    "应用",
    "软件",
    "桌面",
    "当前画面",
    "截图",
    "screen",
    "window",
    "desktop",
    "button",
    "menu",
    "panel",
    "dialog",
    "tab",
    "app",
)

ROUTE_NAMES = (
    "direct_answer",
    "weather",
    "news",
    "web_search",
    "knowledge_lookup",
    "system_ops",
    "document_editor",
    "screen_understanding",
    "agent_shell",
)

INTENT_TO_ROUTE = {
    "direct_answer": "direct_answer",
    "general_chat": "direct_answer",
    "location": "web_search",
    "weather": "weather",
    "news": "news",
    "realtime_info": "web_search",
    "knowledge_lookup": "knowledge_lookup",
    "system_ops": "system_ops",
    "local_action": "agent_shell",
    "ambiguous": "direct_answer",
    "needs_clarification": "direct_answer",
}


@dataclass(slots=True)
class RouteContext:
    previous_skill: str = ""
    previous_structured: dict[str, Any] = field(default_factory=dict)
    screen_followup_remaining: int = 0
    session_id: str = ""
    perception_intent: str = ""
    preferred_routes: list[str] = field(default_factory=list)
    preferred_modalities: list[str] = field(default_factory=list)
    active_focus: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RouteCandidate:
    name: str
    score: float
    reason: str
    skill_name: str = ""
    allowed_tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoutingDecision:
    primary_intent: str
    tool_needed: bool
    clarification_needed: bool
    reason: str
    planner_confidence: float = 0.0
    candidates: list[RouteCandidate] = field(default_factory=list)
    selected_tool: str = "direct_answer"


@dataclass(slots=True)
class LLMRouteDecision:
    primary_intent: str
    reason: str
    confidence: float = 0.0
    tool_needed: bool = False
    clarification_needed: bool = False
    candidate_scores: dict[str, float] = field(default_factory=dict)


class SkillRouter:
    def __init__(self, specs: Iterable[SkillSpec]) -> None:
        self.specs = {spec.name: spec for spec in specs}

    def decide(
        self,
        user_request: str,
        attachment_paths: Iterable[str] | None = None,
        context: RouteContext | None = None,
        llm_decision: LLMRouteDecision | None = None,
    ) -> RoutingDecision:
        text = user_request.strip()
        lowered = text.lower()
        compact = re.sub(r"\s+", "", lowered)
        attachments = [str(path) for path in (attachment_paths or [])]
        attachment_suffixes = {Path(path).suffix.lower() for path in attachments}
        context = context or RouteContext()

        explicit_location = self._looks_like_location_task(lowered, compact, context)
        explicit_map_display = self._looks_like_map_display_request(lowered, context)
        explicit_screen = self._looks_like_screen_task(lowered)
        sticky_screen = self._should_continue_screen_context(
            lowered,
            attachments,
            context,
            explicit_location=explicit_location,
            explicit_map_display=explicit_map_display,
        )
        explicit_document = bool(attachments) or self._looks_like_document_task(lowered, attachment_suffixes)
        explicit_agent = self._looks_like_agent_task(lowered)
        explicit_weather = self._looks_like_weather_task(lowered)
        explicit_news = self._looks_like_news_task(lowered)
        explicit_news_intelligence = self._looks_like_news_intelligence_task(lowered, compact) or "ithome.com" in lowered
        explicit_knowledge = self._looks_like_knowledge_lookup(lowered)
        explicit_system = self._looks_like_system_ops(lowered)
        explicit_web = self._looks_like_web_task(lowered) or bool(re.search(r"https?://", lowered))
        explicit_realtime = self._looks_like_realtime_lookup(lowered)

        scores: dict[str, float] = {"direct_answer": 0.38}
        reasons: dict[str, list[str]] = {"direct_answer": ["默认保留自然回答兜底。"]}

        def boost(name: str, amount: float, reason: str) -> None:
            if name not in ROUTE_NAMES:
                return
            scores[name] = min(0.99, scores.get(name, 0.0) + max(0.0, amount))
            reasons.setdefault(name, []).append(reason)

        heuristic_intent = self._heuristic_primary_intent(
            explicit_location=explicit_location,
            explicit_screen=explicit_screen,
            sticky_screen=sticky_screen,
            explicit_document=explicit_document,
            explicit_agent=explicit_agent,
            explicit_weather=explicit_weather,
            explicit_news=explicit_news,
            explicit_knowledge=explicit_knowledge,
            explicit_system=explicit_system,
            explicit_web=explicit_web,
            explicit_realtime=explicit_realtime,
        )

        if context.perception_intent == "weather_lookup":
            boost("weather", 0.28, "Perception layer locked weather lookup.")
            boost("web_search", 0.12, "Keep structured weather fallback available.")
        elif context.perception_intent in {"location_lookup", "display_information"}:
            boost("web_search", 0.28, "Perception layer locked location/map rendering path.")
            boost("direct_answer", 0.0, "Structured location path takes precedence over freeform answer.")
        elif context.perception_intent == "news_lookup":
            boost("news", 0.26, "Perception layer locked news retrieval path.")
            boost("web_search", 0.1, "Keep research fallback available for news.")
        elif context.perception_intent == "system_action":
            boost("screen_understanding", 0.22, "Perception layer indicates system action.")
            boost("agent_shell", 0.1, "Keep local action fallback available.")

        for route_name in context.preferred_routes:
            boost(route_name, 0.12, f"Runtime preferred route: {route_name}.")

        if llm_decision is not None:
            for name, value in llm_decision.candidate_scores.items():
                boost(name, min(0.95, max(0.0, float(value))) * 0.72, f"模型判断：{llm_decision.reason or name}")
            preferred = INTENT_TO_ROUTE.get(llm_decision.primary_intent, "")
            if preferred:
                boost(preferred, 0.16, f"模型主意图偏向 {llm_decision.primary_intent}。")
            if not llm_decision.tool_needed:
                boost("direct_answer", 0.18, "模型判断这类问题通常不需要工具。")

        if explicit_location:
            boost("web_search", 0.92, "请求更像地点、地址、地图展示或位置查询。")
            boost("direct_answer", 0.04, "保留自然回答作为位置查询失败时的兜底。")
        if sticky_screen or explicit_screen:
            boost("screen_understanding", 0.88, "当前更像屏幕理解或界面追问。")
        if explicit_document and not explicit_agent:
            boost("document_editor", 0.84, "请求更像文件读取、总结或修改。")
        if explicit_agent:
            boost("agent_shell", 0.86, "请求更像项目代码、命令或实现任务。")
        if explicit_weather and not explicit_agent and not explicit_document:
            boost("weather", 0.92, "语义明确指向天气查询。")
            boost("web_search", 0.18, "天气查询失败时可回退到泛化联网检索。")
        if explicit_news_intelligence and not explicit_agent:
            boost("news", 0.88, "请求更像科技新闻筛选、简报或观察库查询。")
            boost("web_search", 0.16, "新闻路径失败时可回退到联网检索。")
        elif explicit_news and not explicit_weather and not explicit_system:
            boost("news", 0.78, "语义明确指向新闻或头条。")
            boost("web_search", 0.24, "新闻路径失败时可回退到联网检索。")
        if explicit_knowledge:
            boost("knowledge_lookup", 0.82, "请求明确引用了之前讨论、历史决策或项目上下文。")
        if explicit_system:
            boost("system_ops", 0.84, "请求更像 Fairy 自身系统状态、通知或 jobs 查询。")
        if explicit_web and not explicit_location and not explicit_weather and not explicit_news and not explicit_system and not explicit_knowledge:
            boost("web_search", 0.68, "请求明确提到了联网、网页或 URL。")
        if explicit_realtime and not (explicit_location or explicit_weather or explicit_news or explicit_system or explicit_knowledge):
            boost("web_search", 0.18, "问题涉及实时性，但暂时只作为轻量联网候选。")

        if explicit_location and scores.get("web_search", 0.0) <= scores.get("direct_answer", 0.0):
            scores["web_search"] = min(0.99, scores.get("direct_answer", 0.0) + 0.08)
            reasons.setdefault("web_search", []).append("位置相关请求优先交给 location_lookup / map card 路径。")

        if context.perception_intent == "weather_lookup" and scores.get("weather", 0.0) <= scores.get("direct_answer", 0.0):
            scores["weather"] = min(0.99, scores.get("direct_answer", 0.0) + 0.1)
            reasons.setdefault("weather", []).append("Perception layer prevents direct_answer from bypassing weather tool flow.")
        if context.perception_intent in {"location_lookup", "display_information"} and scores.get("web_search", 0.0) <= scores.get("direct_answer", 0.0):
            scores["web_search"] = min(0.99, scores.get("direct_answer", 0.0) + 0.1)
            reasons.setdefault("web_search", []).append("Perception layer prevents direct_answer from bypassing location tool flow.")
        if context.perception_intent == "news_lookup" and scores.get("news", 0.0) <= scores.get("direct_answer", 0.0):
            scores["news"] = min(0.99, scores.get("direct_answer", 0.0) + 0.1)
            reasons.setdefault("news", []).append("Perception layer prevents direct_answer from bypassing news retrieval flow.")

        if not any(name in scores for name in ("screen_understanding", "document_editor", "agent_shell", "weather", "news", "knowledge_lookup", "system_ops", "web_search")):
            boost("direct_answer", 0.12, "未发现必须调用工具的高置信度信号，优先自然回答。")

        preferred_route = INTENT_TO_ROUTE.get(llm_decision.primary_intent if llm_decision else heuristic_intent, "") or INTENT_TO_ROUTE.get(heuristic_intent, "direct_answer")
        if preferred_route:
            boost(preferred_route, 0.08, f"最终主意图更偏向 {llm_decision.primary_intent if llm_decision else heuristic_intent}。")

        if explicit_location and scores.get("web_search", 0.0) <= scores.get("direct_answer", 0.0):
            scores["web_search"] = min(0.99, scores.get("direct_answer", 0.0) + 0.08)
            reasons.setdefault("web_search", []).append("位置相关请求优先交给 location_lookup / map card 路径。")

        candidates: list[RouteCandidate] = []
        for name, score in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
            if score <= 0.0:
                continue
            skill_name = self._candidate_skill_name(name, lowered, compact)
            allowed_tools = self._candidate_allowed_tools(skill_name)
            candidates.append(
                RouteCandidate(
                    name=name,
                    score=round(score, 4),
                    reason=" ".join(reasons.get(name, [])).strip(),
                    skill_name=skill_name,
                    allowed_tools=allowed_tools,
                )
            )

        if not any(candidate.name == "direct_answer" for candidate in candidates):
            candidates.append(RouteCandidate("direct_answer", 0.2, "始终保留 direct_answer 作为回退路径。"))

        candidates.sort(key=lambda item: (-item.score, item.name))
        primary_intent = llm_decision.primary_intent if llm_decision and llm_decision.primary_intent else heuristic_intent
        planner_confidence = llm_decision.confidence if llm_decision is not None else (candidates[0].score if candidates else 0.38)
        tool_needed = self._resolve_tool_needed(primary_intent, candidates, llm_decision)
        clarification_needed = bool(llm_decision.clarification_needed if llm_decision else False)
        selected_tool = candidates[0].name if candidates else "direct_answer"
        if context.perception_intent in {"weather_lookup", "location_lookup", "display_information", "news_lookup"} and selected_tool == "direct_answer":
            logger.warning(
                "structured_route_bypassed perception_intent=%s selected_tool=%s top_reason=%s",
                context.perception_intent,
                selected_tool,
                candidates[0].reason if candidates else "",
            )
        reason = llm_decision.reason if llm_decision and llm_decision.reason else (candidates[0].reason if candidates else "默认走自然回答。")
        return RoutingDecision(
            primary_intent=primary_intent,
            tool_needed=tool_needed,
            clarification_needed=clarification_needed,
            reason=reason,
            planner_confidence=max(0.0, min(1.0, float(planner_confidence or 0.0))),
            candidates=candidates,
            selected_tool=selected_tool,
        )

    def build_attempt_sequence(self, decision: RoutingDecision) -> list[RouteCandidate]:
        candidate_map = {candidate.name: candidate for candidate in decision.candidates}
        fallback_names = {
            "location": ["web_search", "direct_answer"],
            "weather": ["weather", "web_search", "direct_answer"],
            "news": ["news", "web_search", "direct_answer"],
            "knowledge_lookup": ["knowledge_lookup", "system_ops", "direct_answer"],
            "system_ops": ["system_ops", "knowledge_lookup", "direct_answer"],
            "realtime_info": ["web_search", "direct_answer"],
            "screen_understanding": ["screen_understanding", "direct_answer"],
            "document_editor": ["document_editor", "direct_answer"],
            "local_action": ["agent_shell", "direct_answer"],
            "ambiguous": ["direct_answer"],
            "needs_clarification": ["direct_answer"],
            "direct_answer": ["direct_answer"],
            "general_chat": ["direct_answer"],
        }
        ordered_names: list[str] = []
        if decision.selected_tool:
            ordered_names.append(decision.selected_tool)
        for name in fallback_names.get(decision.primary_intent, []):
            ordered_names.append(name)
        for candidate in decision.candidates:
            ordered_names.append(candidate.name)
        if "direct_answer" not in ordered_names:
            ordered_names.append("direct_answer")

        sequence: list[RouteCandidate] = []
        seen: set[str] = set()
        for name in ordered_names:
            if name in seen:
                continue
            seen.add(name)
            candidate = candidate_map.get(name) or self._synthetic_candidate(name)
            if candidate is not None:
                sequence.append(candidate)
        return sequence

    def route(
        self,
        user_request: str,
        attachment_paths: Iterable[str] | None = None,
        context: RouteContext | None = None,
        llm_decision: LLMRouteDecision | None = None,
    ) -> SkillRoute:
        decision = self.decide(
            user_request,
            attachment_paths=attachment_paths,
            context=context,
            llm_decision=llm_decision,
        )
        candidate = self.build_attempt_sequence(decision)[0]
        chosen = candidate.skill_name or candidate.name
        return SkillRoute(chosen, candidate.reason, list(candidate.allowed_tools))

    def _synthetic_candidate(self, name: str) -> RouteCandidate | None:
        if name not in ROUTE_NAMES:
            return None
        skill_name = self._candidate_skill_name(name, "", "")
        return RouteCandidate(
            name=name,
            score=0.12 if name == "direct_answer" else 0.18,
            reason="根据主意图补充回退候选。",
            skill_name=skill_name,
            allowed_tools=self._candidate_allowed_tools(skill_name),
        )

    def _candidate_skill_name(self, name: str, lowered: str, compact: str) -> str:
        if name == "weather":
            return "web_research_skill"
        if name == "web_search":
            return "web_research_skill"
        if name == "news":
            if self._looks_like_news_intelligence_task(lowered, compact) or "科技" in lowered:
                return "news_intelligence_skill"
            return "web_research_skill"
        if name == "document_editor":
            return "document_editor_skill"
        if name == "screen_understanding":
            return "screen_understanding_skill"
        if name == "agent_shell":
            return "agent_shell_skill"
        return ""

    def _candidate_allowed_tools(self, skill_name: str) -> list[str]:
        if not skill_name:
            return []
        spec = self.specs.get(skill_name)
        return list(spec.allowed_tools) if spec is not None else []

    def _resolve_tool_needed(
        self,
        primary_intent: str,
        candidates: list[RouteCandidate],
        llm_decision: LLMRouteDecision | None,
    ) -> bool:
        if llm_decision is not None and llm_decision.confidence >= 0.55:
            return bool(llm_decision.tool_needed)
        if primary_intent in {"location", "weather", "news", "realtime_info", "knowledge_lookup", "system_ops", "local_action"}:
            return True
        top = candidates[0].name if candidates else "direct_answer"
        return top != "direct_answer"

    def _heuristic_primary_intent(
        self,
        *,
        explicit_location: bool,
        explicit_screen: bool,
        sticky_screen: bool,
        explicit_document: bool,
        explicit_agent: bool,
        explicit_weather: bool,
        explicit_news: bool,
        explicit_knowledge: bool,
        explicit_system: bool,
        explicit_web: bool,
        explicit_realtime: bool,
    ) -> str:
        if explicit_location:
            return "location"
        if sticky_screen or explicit_screen:
            return "local_action"
        if explicit_document:
            return "local_action"
        if explicit_agent:
            return "local_action"
        if explicit_weather:
            return "weather"
        if explicit_news:
            return "news"
        if explicit_knowledge:
            return "knowledge_lookup"
        if explicit_system:
            return "system_ops"
        if explicit_web or explicit_realtime:
            return "realtime_info"
        return "direct_answer"

    def _looks_like_web_task(self, lowered: str) -> bool:
        return any(token in lowered for token in WEB_HINTS)

    def _looks_like_realtime_lookup(self, lowered: str) -> bool:
        return any(token in lowered for token in REALTIME_HINTS)

    def _looks_like_news_task(self, lowered: str) -> bool:
        return any(token in lowered for token in NEWS_HINTS)

    def _looks_like_weather_task(self, lowered: str) -> bool:
        return any(token in lowered for token in WEATHER_HINTS)

    def _looks_like_news_intelligence_task(self, lowered: str, compact: str) -> bool:
        if any(token in lowered for token in NEWS_INTELLIGENCE_HINTS):
            return True
        if "it之家" in compact or "ithome" in compact:
            return True
        if "观察库" in compact or "技术观察" in compact:
            return True
        if "科技简报" in compact or ("值得看" in compact and "新闻" in compact):
            return True
        if "fairy项目相关" in compact or ("项目相关" in compact and "新闻" in compact):
            return True
        return False

    def _looks_like_knowledge_lookup(self, lowered: str) -> bool:
        return any(token in lowered for token in KNOWLEDGE_HINTS)

    def _looks_like_system_ops(self, lowered: str) -> bool:
        return any(token in lowered for token in SYSTEM_HINTS)

    def _looks_like_document_task(self, lowered: str, suffixes: set[str]) -> bool:
        if suffixes & {".txt", ".md", ".json", ".csv", ".py", ".yaml", ".yml"}:
            return True
        return any(token in lowered for token in DOCUMENT_HINTS)

    def _has_location_context(self, context: RouteContext) -> bool:
        structured = context.previous_structured if isinstance(context.previous_structured, dict) else {}
        if not structured:
            return False
        if structured.get("lat") is not None and structured.get("lon") is not None:
            return True
        if any(str(structured.get(key, "") or "").strip() for key in ("map_url", "address", "title", "place_name", "image_url")):
            return True
        intent_payload = structured.get("intent") if isinstance(structured.get("intent"), dict) else {}
        if str(intent_payload.get("intent_type", "") or "").strip() == "location_lookup":
            return True
        routing = structured.get("routing") if isinstance(structured.get("routing"), dict) else {}
        return str(routing.get("primary_intent", "") or "").strip() == "location"

    def _looks_like_map_display_request(self, lowered: str, context: RouteContext) -> bool:
        if any(token in lowered for token in MAP_DISPLAY_HINTS):
            return True
        if self._has_location_context(context):
            normalized = re.sub(r"[\s，。！？,.!?]+", "", lowered)
            if normalized in {"地图", "map", "地图呢", "显示地图", "地图显示出来"}:
                return True
            if "地图" in lowered and any(token in lowered for token in ("显示", "展示", "看看", "打开", "预览")):
                return True
        return False

    def _looks_like_desktop_control_request(self, lowered: str) -> bool:
        has_control_verb = any(token in lowered for token in UI_CONTROL_VERBS)
        has_screen_target = any(token in lowered for token in SCREEN_ELEMENT_HINTS)
        return has_control_verb and has_screen_target

    def _looks_like_location_task(self, lowered: str, compact: str, context: RouteContext) -> bool:
        if self._looks_like_map_display_request(lowered, context):
            return True
        if any(token in lowered for token in LOCATION_HINTS):
            return True
        if re.search(r"\bwhere\s+is\b", lowered):
            return True
        if re.search(r"[\u4e00-\u9fffA-Za-z0-9\s]{2,40}(在哪里|在哪|地址|位置)$", lowered):
            return True
        if self._has_location_context(context) and compact in {"地图", "map"}:
            return True
        return False

    def _looks_like_screen_task(self, lowered: str) -> bool:
        if any(token in lowered for token in ("地图", "map")) and not any(token in lowered for token in SCREEN_ELEMENT_HINTS):
            return False
        if self._looks_like_desktop_control_request(lowered):
            return True
        return any(token in lowered for token in SCREEN_HINTS)

    def _looks_like_agent_task(self, lowered: str) -> bool:
        return any(token in lowered for token in AGENT_HINTS)

    def _should_continue_screen_context(
        self,
        lowered: str,
        attachments: list[str],
        context: RouteContext,
        *,
        explicit_location: bool = False,
        explicit_map_display: bool = False,
    ) -> bool:
        if attachments:
            return False
        if context.previous_skill != "screen_understanding_skill":
            return False
        if context.screen_followup_remaining <= 0:
            return False
        if self._looks_like_document_task(lowered, set()):
            return False
        if self._looks_like_weather_task(lowered):
            return False
        if self._looks_like_news_task(lowered):
            return False
        if explicit_location or explicit_map_display:
            return False
        if self._looks_like_web_task(lowered) or re.search(r"https?://", lowered):
            return False
        if self._looks_like_agent_task(lowered):
            return False
        if self._looks_like_desktop_control_request(lowered):
            return True

        has_screen_state = bool((context.previous_structured or {}).get("screen_summary"))
        if not has_screen_state:
            return False

        if any(token in lowered for token in SCREEN_FOLLOWUP_HINTS):
            return True
        if len(lowered) <= 80:
            return True
        return False
