from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.config import news_config
from app.news.news_models import NewsAnalysis, NewsArticle


TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai_llm": ("openai", "claude", "gemini", "qwen", "deepseek", "大模型", "llm", "chatgpt", "copilot"),
    "local_model": ("本地模型", "端侧模型", "llama", "gguf", "vllm", "npu", "推理"),
    "apple": ("apple", "苹果", "mac", "macos", "iphone", "ipad", "siri", "vision pro"),
    "windows": ("windows", "微软", "microsoft", "copilot pc", "win11", "win12"),
    "pc_hardware": ("pc", "台式机", "笔记本", "cpu", "主板", "内存", "ssd", "显示器", "芯片"),
    "gpu": ("gpu", "显卡", "nvidia", "amd", "rtx", "cuda", "geforce", "radeon"),
    "gaming": ("游戏", "steam", "ps5", "xbox", "任天堂", "手游"),
    "agent": ("agent", "智能体", "自动化", "工作流", "tool use", "工具调用"),
    "voice_tts": ("tts", "语音克隆", "实时语音", "语音合成", "文本转语音", "text to speech"),
    "multimodal": ("多模态", "ocr", "屏幕理解", "文档理解", "图像理解", "视觉"),
    "developer_tools": ("开发者", "ide", "vscode", "cursor", "jetbrains", "api", "sdk", "插件"),
    "browser_tools": ("浏览器", "chrome", "edge", "playwright", "selenium", "网页自动化"),
}

PROJECT_TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "desktop assistant": ("桌面助手", "桌宠", "desktop assistant"),
    "voice assistant": ("语音助手", "tts", "文本转语音"),
    "realtime tts": ("实时语音", "流式语音", "语音合成"),
    "memory system": ("记忆", "memory", "向量库", "长期记忆"),
    "tool use": ("工具调用", "tool use", "自动化", "工作流"),
    "screen understanding": ("屏幕理解", "截图分析", "ocr", "界面理解"),
    "document understanding": ("文档理解", "文档解析", "markdown", "pdf"),
    "browser automation": ("浏览器自动化", "playwright", "网页自动化"),
    "local model": ("本地模型", "端侧模型", "gguf", "llama"),
    "provider abstraction": ("provider", "插件", "mcp", "skill", "扩展"),
}

OBSERVE_BRANDS = ("apple", "苹果", "microsoft", "微软", "nvidia", "英伟达", "openai", "google", "谷歌", "meta", "qwen", "deepseek")
HYPE_KEYWORDS = ("爆料", "曝光", "传闻", "网友", "热搜", "开箱图", "首发图", "纯爆料", "娱乐化标题", "无实质参数")
RELEASE_KEYWORDS = ("发布", "上线", "更新", "开源", "sdk", "api", "芯片", "新品", "版本", "路线图")


@dataclass(slots=True)
class NewsProfile:
    preferred_tags: tuple[str, ...]
    project_topics: tuple[str, ...]
    skip_keywords: tuple[str, ...]


class RuleBasedNewsClassifier:
    def __init__(self, profile: NewsProfile | None = None) -> None:
        self.profile = profile or NewsProfile(
            preferred_tags=tuple(news_config.preferred_tags),
            project_topics=tuple(news_config.project_topics),
            skip_keywords=tuple(news_config.skip_keywords),
        )

    def analyze(self, article: NewsArticle) -> NewsAnalysis:
        weighted_hits = self._collect_weighted_hits(article)
        tag_scores = self._match_tags(weighted_hits)
        tags = sorted(tag for tag, score in tag_scores.items() if score > 0)
        article.tags = tags

        matched_interests = [tag for tag in tags if tag in self.profile.preferred_tags]
        matched_project_topics = self._match_project_topics(weighted_hits)
        title_text = (article.title or "").lower()
        summary_text = (article.summary or "").lower()
        content_text = (article.content or "").lower()
        full_text = " ".join(part for part in (title_text, summary_text, content_text) if part)

        title_hits = sum(weight for token, weight in weighted_hits.items() if token in title_text)
        summary_hits = sum(weight for token, weight in weighted_hits.items() if token in summary_text)
        content_hits = sum(weight for token, weight in weighted_hits.items() if token in content_text)

        relevance_score = min(1.0, 0.18 * len(matched_interests) + 0.03 * title_hits + 0.015 * summary_hits + 0.008 * content_hits)
        project_tag_hits = len({"agent", "voice_tts", "multimodal", "developer_tools", "browser_tools", "local_model", "ai_llm"} & set(tags))
        project_relevance_score = min(1.0, 0.22 * len(matched_project_topics) + 0.1 * project_tag_hits + 0.06 * title_hits)

        observe_signal = 0.0
        if any(keyword in full_text for keyword in OBSERVE_BRANDS):
            observe_signal += 0.28
        if any(keyword in full_text for keyword in RELEASE_KEYWORDS):
            observe_signal += 0.26
        observe_signal += min(0.32, project_relevance_score * 0.45 + relevance_score * 0.25)
        observe_score = min(1.0, observe_signal)

        hype_hits = sum(1 for token in (*HYPE_KEYWORDS, *self.profile.skip_keywords) if token.lower() in full_text)
        hype_score = min(1.0, 0.24 * hype_hits + (0.18 if "！" in article.title or "!" in article.title else 0.0))

        reasons = self._build_reasons(tags, matched_project_topics, observe_score, hype_score, title_text)
        action = self._decide_action(relevance_score, project_relevance_score, observe_score, hype_score)
        short_comment = self._build_short_comment(action, tags, matched_project_topics)

        return NewsAnalysis(
            relevance_score=round(relevance_score, 4),
            project_relevance_score=round(project_relevance_score, 4),
            observe_score=round(observe_score, 4),
            hype_score=round(hype_score, 4),
            action=action,
            reasons=reasons,
            matched_interests=matched_interests,
            matched_project_topics=matched_project_topics,
            short_comment=short_comment,
        )

    def _collect_weighted_hits(self, article: NewsArticle) -> dict[str, float]:
        title = (article.title or "").lower()
        summary = (article.summary or "").lower()
        weighted = defaultdict(float)
        for keywords in (*TAG_KEYWORDS.values(), *PROJECT_TOPIC_RULES.values()):
            for keyword in keywords:
                key = keyword.lower()
                if self._contains_keyword(title, key):
                    weighted[key] += 3.0
                if self._contains_keyword(summary, key):
                    weighted[key] += 1.8
        return dict(weighted)

    def _match_tags(self, weighted_hits: dict[str, float]) -> dict[str, float]:
        tag_scores: dict[str, float] = {}
        for tag, keywords in TAG_KEYWORDS.items():
            score = sum(weighted_hits.get(keyword.lower(), 0.0) for keyword in keywords)
            if score > 0:
                tag_scores[tag] = min(1.0, score / 5.0)
        return tag_scores

    def _match_project_topics(self, weighted_hits: dict[str, float]) -> list[str]:
        matched: list[str] = []
        for topic, keywords in PROJECT_TOPIC_RULES.items():
            score = sum(weighted_hits.get(keyword.lower(), 0.0) for keyword in keywords)
            if score >= 2.0:
                matched.append(topic)
        return matched

    def _decide_action(self, relevance: float, project_relevance: float, observe: float, hype: float) -> str:
        if project_relevance >= 0.72:
            return "project_related"
        if observe >= 0.68 and hype < 0.78:
            return "observe"
        if relevance >= 0.36 and hype < 0.8:
            return "read"
        return "skip"

    def _build_reasons(self, tags: list[str], matched_project_topics: list[str], observe_score: float, hype_score: float, title_text: str) -> list[str]:
        reasons: list[str] = []
        if tags:
            reasons.append("命中标签：" + " / ".join(tags[:4]))
        if matched_project_topics:
            reasons.append("与 Fairy 项目相关：" + " / ".join(matched_project_topics[:3]))
        if observe_score >= 0.65:
            reasons.append("平台级变化，适合进入技术观察库")
        if hype_score >= 0.7:
            reasons.append("标题偏流量向，技术含量有限")
        if any(word in title_text for word in ("发布", "更新", "上线", "开源")):
            reasons.append("包含明确发布或更新信号")
        return reasons[:4]

    def _build_short_comment(self, action: str, tags: list[str], topics: list[str]) -> str:
        if action == "project_related":
            return "这条和 Fairy 的能力边界直接相关，建议优先阅读。"
        if action == "observe":
            return "短期不必立刻处理，但值得放进观察库持续跟踪。"
        if action == "read":
            if tags:
                return f"这条命中 {', '.join(tags[:3])}，值得快速过一遍。"
            return "这条有一定参考价值，建议速读。"
        if topics:
            return "虽然相关性不强，但当前更像背景噪音，先放一边。"
        return "偏流量或信息密度不足，可以跳过。"

    def _contains_keyword(self, haystack: str, keyword: str) -> bool:
        if not haystack or not keyword:
            return False
        if self._is_ascii_keyword(keyword):
            return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack) is not None
        return keyword in haystack

    def _is_ascii_keyword(self, keyword: str) -> bool:
        return all(ord(ch) < 128 for ch in keyword)
