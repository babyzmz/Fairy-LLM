from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

from app.config import memory_config


NOISE_ASSISTANT_MARKERS = (
    "本地大模型服务未就绪",
    "本地大模型暂未连接",
    "启动错误",
    "llama.cpp server",
    "无法直接获取",
    "无法直接访问",
    "建议访问",
    "请前往 Bureau of Meteorology 官网",
)

TIME_SENSITIVE_MARKERS = (
    "今天",
    "昨日",
    "昨天",
    "明天",
    "最近",
    "最新",
    "现在",
    "目前",
    "实时",
    "刚刚",
    "近期",
)


@dataclass
class MemoryItem:
    user_text: str
    assistant_text: str
    tags: List[str]


class SimpleMemoryStore:
    """
    Lightweight JSON memory store.

    Strategy:
    - Persist each turn.
    - Build memory hints from user-side statements first.
    - Do not trust assistant-generated profile guesses as user facts.
    """

    def __init__(self, path: Path | None = None, max_items: int | None = None):
        self.path = path or memory_config.memory_file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_items = max_items or memory_config.max_items
        self._items: List[MemoryItem] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            loaded: List[MemoryItem] = []
            for item in raw:
                loaded.append(
                    MemoryItem(
                        user_text=str(item.get("user_text", "")).strip(),
                        assistant_text=str(item.get("assistant_text", "")).strip(),
                        tags=list(item.get("tags", [])),
                    )
                )
            cleaned = self._prune_noise(loaded)
            self._items = cleaned
            if len(cleaned) != len(loaded):
                self._save()
        except Exception:  # noqa: BLE001
            self._items = []

    def _save(self) -> None:
        data = [asdict(item) for item in self._items[-self.max_items :]]
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _is_noise_assistant(self, text: str) -> bool:
        return any(marker in text for marker in NOISE_ASSISTANT_MARKERS)

    def _is_time_sensitive(self, text: str) -> bool:
        return any(marker in text for marker in TIME_SENSITIVE_MARKERS)

    def _prune_noise(self, items: List[MemoryItem]) -> List[MemoryItem]:
        cleaned: List[MemoryItem] = []
        for item in items:
            if self._is_noise_assistant(item.assistant_text):
                continue
            if self._is_time_sensitive(item.user_text) and (
                "截至 2024" in item.assistant_text
                or "无需再次联网" in item.assistant_text
                or "非实时信息" in item.assistant_text
            ):
                continue
            cleaned.append(item)
        return cleaned

    def _is_question(self, text: str) -> bool:
        s = text.strip()
        if not s:
            return False
        if "?" in s or "？" in s:
            return True
        return s.endswith(("吗", "呢", "嘛", "么"))

    def add(self, user_text: str, assistant_text: str, tags: List[str] | None = None) -> None:
        item = MemoryItem(user_text=user_text, assistant_text=assistant_text, tags=tags or [])
        self._items.append(item)
        self._items = self._prune_noise(self._items)[-self.max_items :]
        self._save()

    def query_related(self, user_text: str, top_k: int = 5) -> str:  # noqa: ARG002
        if not self._items:
            return ""

        facts: List[str] = []
        topics: List[str] = []
        seen_facts: set[str] = set()
        seen_topics: set[str] = set()

        for item in reversed(self._items):
            user_msg = item.user_text.strip()
            if not user_msg:
                continue
            if self._is_time_sensitive(user_msg):
                continue

            if self._is_question(user_msg):
                if user_msg not in seen_topics:
                    topics.append(user_msg)
                    seen_topics.add(user_msg)
            else:
                if user_msg not in seen_facts:
                    facts.append(user_msg)
                    seen_facts.add(user_msg)

            if len(facts) >= top_k and len(topics) >= top_k:
                break

        lines: List[str] = []
        if facts:
            lines.append("[已确认的用户陈述]")
            for x in facts[:top_k]:
                lines.append(f"- {x}")

        if topics:
            if lines:
                lines.append("")
            lines.append("[最近聊过的话题]")
            for x in topics[:top_k]:
                lines.append(f"- {x}")

        return "\n".join(lines)
