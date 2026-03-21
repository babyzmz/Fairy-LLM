from __future__ import annotations

import json
import logging
import re
from typing import Callable, Iterable

from app.ai.llm_client import LLMClient

from .dataset_models import CATEGORY_CHOICES, EMOTION_CHOICES, LENGTH_CHOICES, TrainingEntry
from .dataset_parser import build_duplicate_counts


logger = logging.getLogger(__name__)

_SARCASM_KEYWORDS = ("发呆", "拖延", "偷懒", "放弃思考", "可疑", "艺术品", "马桶", "快递")
_SYSTEM_KEYWORDS = ("系统", "模块", "启动", "初始化", "校准", "在线", "运行正常", "待机", "核心链路")
_ANALYSIS_KEYWORDS = ("分析", "检索", "检测", "识别", "判断", "视觉模块", "数据")
_INSTRUCTION_KEYWORDS = ("请", "建议", "提供", "执行", "输入", "确认")
_GREETING_KEYWORDS = ("你好", "早上好", "晚上好", "欢迎")
_OBSERVATION_KEYWORDS = ("检测到", "似乎", "观察", "摄像头", "当前", "状态")
_CONFIDENCE_KEYWORDS = ("已确认", "已完成", "运行正常", "已恢复在线", "已激活", "已接入")


def enrich_dataset_with_llm(
    entries: Iterable[TrainingEntry],
    *,
    progress_callback: Callable[[int, int, TrainingEntry], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> list[TrainingEntry]:
    return DatasetLLMEnricher().enrich_entries(
        list(entries),
        progress_callback=progress_callback,
        stop_requested=stop_requested,
    )


class DatasetLLMEnricher:
    def __init__(self) -> None:
        self._client: LLMClient | None = None

    def enrich_entries(
        self,
        entries: list[TrainingEntry],
        *,
        progress_callback: Callable[[int, int, TrainingEntry], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> list[TrainingEntry]:
        logger.info("dataset_llm_enrich_start count=%s", len(entries))
        duplicates = build_duplicate_counts(entries)
        result: list[TrainingEntry] = []
        try:
            self._client = LLMClient()
            for index, entry in enumerate(entries, start=1):
                if stop_requested and stop_requested():
                    logger.info("dataset_llm_enrich_done count=%s cancelled=true", len(result))
                    return result + entries[index - 1 :]
                updated = self._enrich_single(entry, duplicates)
                result.append(updated)
                if progress_callback is not None:
                    progress_callback(index, len(entries), updated)
        finally:
            if self._client is not None:
                self._client.shutdown()
                self._client = None
        logger.info("dataset_llm_enrich_done count=%s", len(result))
        return result

    def _enrich_single(self, entry: TrainingEntry, duplicates: dict[str, int]) -> TrainingEntry:
        heuristic = _heuristic_annotation(entry, duplicates)
        client = self._client
        if client is None:
            return heuristic

        prompt = _build_prompt(entry.text, heuristic.note)
        response = client.chat([], prompt)
        payload = _extract_json_object(response.text)
        if payload is None:
            return heuristic

        annotated = TrainingEntry.from_dict(heuristic.to_dict())
        annotated.category = _pick_allowed(payload.get("category"), CATEGORY_CHOICES) or heuristic.category
        annotated.length_type = _pick_allowed(payload.get("length_type"), LENGTH_CHOICES) or heuristic.length_type
        annotated.emotion = _pick_allowed(payload.get("emotion"), EMOTION_CHOICES) or heuristic.emotion
        annotated.note = _merge_notes(heuristic.note, str(payload.get("note", "") or ""))
        if annotated.status not in {"generated", "failed"}:
            annotated.status = "tagged"
        return annotated


def _build_prompt(text: str, heuristic_note: str) -> str:
    return (
        "你是 Fairy 训练语料整理器。"
        "不要改写文本，不要扩写，不要润色。"
        "你的任务只是在给单条训练语料做标签。"
        "只输出一个 JSON 对象，不要输出 Markdown，不要输出解释。\n\n"
        "允许值：\n"
        f"category: {', '.join(v for v in CATEGORY_CHOICES if v)}\n"
        f"length_type: {', '.join(v for v in LENGTH_CHOICES if v)}\n"
        f"emotion: {', '.join(v for v in EMOTION_CHOICES if v)}\n\n"
        "JSON 格式："
        '{"category":"system","length_type":"short","emotion":"calm","note":"如果有训练风险就写在这里，没有就留空"}\n\n'
        f"文本：{text}\n"
        f"已有规则提示：{heuristic_note or '无'}"
    )


def _extract_json_object(text: str) -> dict[str, str] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): value for key, value in payload.items()}


def _heuristic_annotation(entry: TrainingEntry, duplicates: dict[str, int]) -> TrainingEntry:
    annotated = TrainingEntry.from_dict(entry.to_dict())
    text = annotated.text.strip()
    length = len(text)

    annotated.length_type = _classify_length(length)
    annotated.category = _classify_category(text, annotated.length_type)
    annotated.emotion = _classify_emotion(text)
    annotated.note = _build_note(text, duplicates.get(_normalize_key(text), 0), annotated.length_type)
    if annotated.status not in {"generated", "failed"}:
        annotated.status = "tagged"
    return annotated


def _classify_length(length: int) -> str:
    if length <= 14:
        return "short"
    if length <= 34:
        return "medium"
    return "long"


def _classify_category(text: str, length_type: str) -> str:
    if any(keyword in text for keyword in _GREETING_KEYWORDS):
        return "greeting"
    if any(keyword in text for keyword in _SARCASM_KEYWORDS):
        return "sarcasm"
    if any(keyword in text for keyword in _ANALYSIS_KEYWORDS):
        return "analysis"
    if any(keyword in text for keyword in _INSTRUCTION_KEYWORDS):
        return "instruction"
    if any(keyword in text for keyword in _OBSERVATION_KEYWORDS):
        return "observation"
    if any(keyword in text for keyword in _SYSTEM_KEYWORDS):
        return "system"
    if length_type == "long":
        return "long_reasoning"
    return "calm_response"


def _classify_emotion(text: str) -> str:
    if any(keyword in text for keyword in _SARCASM_KEYWORDS):
        return "slight_sarcasm"
    if any(keyword in text for keyword in _CONFIDENCE_KEYWORDS):
        return "confidence"
    if any(keyword in text for keyword in _SYSTEM_KEYWORDS + _ANALYSIS_KEYWORDS):
        return "cold"
    if text.endswith("？") or text.endswith("?"):
        return "neutral"
    return "calm"


def _build_note(text: str, duplicate_count: int, length_type: str) -> str:
    notes: list[str] = []
    if duplicate_count > 1:
        notes.append("疑似重复语料。")
    if length_type == "long":
        notes.append("句子偏长，后续训练前可考虑拆分。")
    if re.search(r"(^#|//|/\*|\*/|TODO|注释)", text, re.I):
        notes.append("像注释或开发说明，可能不适合直接作为角色语料。")
    if len(text) <= 2:
        notes.append("文本过短，训练价值有限。")
    return " ".join(notes).strip()


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().lower()


def _pick_allowed(value: object, allowed: list[str]) -> str | None:
    text = str(value or "").strip()
    return text if text in allowed else None


def _merge_notes(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not right:
        return left
    if not left:
        return right
    if right in left:
        return left
    return f"{left} {right}".strip()
