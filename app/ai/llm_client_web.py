from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable

from app.tools.web.research_pipeline import run_web_research


Message = dict[str, Any]

TIME_SENSITIVE_MARKERS = (
    "今天",
    "今日",
    "昨天",
    "昨日",
    "明天",
    "现在",
    "目前",
    "最近",
    "最新",
    "实时",
    "刚刚",
    "近期",
    "本周",
    "本月",
    "今年",
    "目前为止",
    "到现在",
    "latest",
    "current",
    "today",
    "recent",
    "newest",
    "real-time",
)

DYNAMIC_TOPIC_MARKERS = (
    "天气",
    "气温",
    "新闻",
    "股价",
    "汇率",
    "价格",
    "版本",
    "更新",
    "补丁",
    "角色",
    "卡池",
    "上线",
    "发布",
    "发售",
    "票房",
    "销量",
    "官网",
    "hotfix",
    "patch",
    "banner",
    "release",
    "weather",
    "price",
    "stock",
    "exchange rate",
)

GENERIC_WEB_FOLLOWUPS = (
    "联网找一下",
    "联网查一下",
    "上网找一下",
    "上网查一下",
    "搜一下",
    "查一下",
    "联网",
    "上网",
)
GENERIC_WEB_FOLLOWUPS_LOWER = {item.lower() for item in GENERIC_WEB_FOLLOWUPS}


class WebSearchSupport:
    def __init__(
        self,
        config: Any,
        post_chat_completion: Callable[[list[Message], int, float], Any],
    ) -> None:
        self.config = config
        self._post_chat_completion = post_chat_completion

    def build_web_decision_prompt(self, user_input: str) -> list[Message]:
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            {
                "role": "system",
                "content": (
                    "你是一个联网决策器。"
                    f"当前现实日期是 {today}。"
                    "判断当前用户问题是否必须联网检索。"
                    "涉及新闻、最近事件、实时信息、互联网资料、价格、官网、版本更新、不确定事实、今天、最新、现在、目前、最近、实时时，输出 YES。"
                    "属于常识、编程、数学、解释、改写、总结时，输出 NO。"
                    "只输出 YES 或 NO。"
                ),
            },
            {"role": "user", "content": user_input.strip() or "(empty)"},
        ]

    def requires_fresh_web_lookup(self, user_input: str) -> bool:
        lowered = user_input.lower()
        if any(marker in lowered for marker in TIME_SENSITIVE_MARKERS):
            return True
        if any(marker in lowered for marker in DYNAMIC_TOPIC_MARKERS):
            return True
        if re.search(r"\b20\d{2}\b", user_input):
            return True
        return False

    def web_search_fallback(self, user_input: str) -> bool:
        lowered = user_input.lower()
        positive_markers = TIME_SENSITIVE_MARKERS + DYNAMIC_TOPIC_MARKERS + (
            "联网",
            "网上",
            "发布时间",
            "最新消息",
            "official",
        )
        negative_markers = (
            "python",
            "代码",
            "编程",
            "算法",
            "解释",
            "什么意思",
            "翻译",
            "数学",
            "方程",
            "润色",
            "重写",
        )
        if any(marker in lowered for marker in positive_markers):
            return True
        if any(marker in lowered for marker in negative_markers):
            return False
        return False

    def build_web_query(self, user_input: str) -> str:
        query = user_input.strip()
        year = datetime.now().strftime("%Y")
        if not query:
            return query
        if "原神" in query and "角色" in query and self.requires_fresh_web_lookup(query):
            query = f"{query} site:hoyolab.com OR site:genshin.hoyoverse.com 角色 角色介绍"
        if self.requires_fresh_web_lookup(query) and year not in query:
            return f"{query} {year}"
        return query

    def preferred_domains_for_query(self, query: str) -> list[str]:
        lowered = query.lower()
        if "原神" in query or "genshin" in lowered:
            return ["hoyolab.com", "genshin.hoyoverse.com"]
        if "天气" in query or "weather" in lowered:
            if any(token in lowered for token in ("melbourne", "墨尔本", "australia", "澳大利亚")):
                return ["bom.gov.au", "weather.com", "accuweather.com"]
            return ["weather.com", "accuweather.com"]
        return []

    def build_web_queries(self, user_input: str) -> list[str]:
        base = user_input.strip()
        if not base:
            return []
        year = datetime.now().strftime("%Y")
        queries: list[str] = []

        def add(query: str) -> None:
            cleaned = re.sub(r"\s+", " ", query).strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

        primary = self.build_web_query(base)
        add(primary)
        if year not in primary and self.requires_fresh_web_lookup(base):
            add(f"{base} {year}")

        preferred_domains = self.preferred_domains_for_query(base)
        for domain in preferred_domains[:2]:
            add(f"{base} site:{domain}")
            if self.requires_fresh_web_lookup(base) and year not in base:
                add(f"{base} {year} site:{domain}")

        if "原神" in base and "角色" in base:
            add(f"原神 最新 角色 {year} site:hoyolab.com/article")
            add(f"原神 新角色 {year} site:hoyolab.com/article")
            add(f"原神 版本更新 {year} site:genshin.hoyoverse.com/zh-cn/news/detail")
            add(f"原神 角色介绍 {year} site:genshin.hoyoverse.com/zh-cn/news/detail")
        elif ("天气" in base or "weather" in base.lower()) and year not in base:
            add(f"{base} {year} today")

        return queries[:4]

    def build_web_failure_prompt(self, web_basis: str) -> str:
        return (
            "\n\n本轮问题属于需要联网确认的时效性问题，但本次联网检索未获得足够的可用结果。"
            "禁止宣称已经确认答案。"
            "禁止虚构‘离线状态’、‘数据库不可达’、‘官网无法访问’等未被系统明确提供的原因。"
            "禁止使用模型旧知识去冒充当前事实。"
            "你只能明确说明：本次联网检索未获得足够结果。"
            "然后给出一个简短建议，例如让用户稍后重试、换更具体关键词，或指定官方来源。\n"
            f"本次检索主题：{web_basis}"
        )

    def is_generic_web_followup(self, text: str) -> bool:
        cleaned = re.sub(r"\s+", "", text.strip().lower())
        if not cleaned:
            return False
        if cleaned in GENERIC_WEB_FOLLOWUPS_LOWER:
            return True
        return len(cleaned) <= 8 and any(token in cleaned for token in ("联网", "上网", "查", "搜"))

    def resolve_web_basis(self, history: Iterable[Message], user_input: str) -> str:
        current = user_input.strip()
        if not self.is_generic_web_followup(current):
            return current
        for item in reversed(list(history)):
            if item.get("role") != "user":
                continue
            content = item.get("content", "")
            if not isinstance(content, str):
                continue
            candidate = content.strip()
            if not candidate or self.is_generic_web_followup(candidate):
                continue
            return candidate
        return current

    def should_use_web(self, user_input: str) -> bool:
        if not self.config.enable_web_search:
            return False
        if not user_input.strip():
            return False
        if self.requires_fresh_web_lookup(user_input):
            return True
        try:
            resp = self._post_chat_completion(
                self.build_web_decision_prompt(user_input),
                max_tokens=self.config.web_decision_max_tokens,
                temperature=0.0,
            )
            if resp.ok:
                data = resp.json()
                text = str(data["choices"][0]["message"]["content"]).strip().upper()
                if text.startswith("YES"):
                    return True
                if text.startswith("NO"):
                    return False
        except Exception:
            pass
        return self.web_search_fallback(user_input)

    def build_web_context(
        self,
        user_input: str,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, int]:
        search_queries = self.build_web_queries(user_input)
        preferred_domains = self.preferred_domains_for_query(user_input)
        try:
            research = run_web_research(
                search_queries,
                max_results=self.config.web_search_max_results,
                max_pages=self.config.web_read_max_pages,
                max_chars=self.config.web_page_max_chars,
                timeout_sec=self.config.web_request_timeout_sec,
                preferred_domains=preferred_domains,
                progress_callback=(
                    (lambda event, _: status_callback(event)) if status_callback is not None else None
                ),
            )
        except Exception:
            return "", 0

        pages = research.pages
        if not pages:
            return "", 0

        if status_callback is not None:
            status_callback("information_found")

        blocks: list[str] = []
        total_chars = 0
        for index, page in enumerate(pages, start=1):
            block = (
                f"[Web Result {index}]\n"
                f"Search Query: {page.query}\n"
                f"Provider: {page.provider}\n"
                f"Title: {page.title}\n"
                f"URL: {page.url}\n"
                f"Content:\n{page.content}"
            )
            if total_chars + len(block) > self.config.web_context_total_chars:
                remaining = max(0, self.config.web_context_total_chars - total_chars)
                if remaining <= 0:
                    break
                block = block[:remaining]
            blocks.append(block)
            total_chars += len(block)
            if total_chars >= self.config.web_context_total_chars:
                break
        return "\n\n".join(blocks).strip(), len(blocks)
