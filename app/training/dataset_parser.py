from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .dataset_models import TrainingEntry


DEFAULT_FAIRY_TEMPLATE = """1
系统启动完成。

2
核心模块运行正常。

3
你好，主人。

4
Fairy 已接入当前任务。

5
请求已接收。

6
正在分析问题。

7
系统检测到新的请求。

8
视觉模块已激活。

9
正在检索网络信息。

10
主人，您已经放弃了思考吗。

11
主人，您发呆的样子真好看，相信我，我链接了高清摄像头。

12
叮咚，门口有您的快递，没有回应，连快递都无法唤醒您。

13
主人，您发呆的姿态简直就是艺术品，堪比古典雕塑，马桶上的沉思者。

14
我正在模仿您的声音，安抚小队成员，但某位成员说，我说话的方式很可疑。
"""


_PURE_NUMBER_RE = re.compile(r"^\s*(\d{1,6})\s*$")
_INLINE_NUMBER_RE = re.compile(r"^\s*(?:[(（]?\s*(\d{1,6})\s*[)）]?[\.\、:：\-]?\s*)(.+?)\s*$")
_SEPARATOR_RE = re.compile(r"^[=\-#*_~·•\s]{3,}$")


def parse_dataset_with_rules(raw_text: str) -> list[TrainingEntry]:
    entries: list[TrainingEntry] = []
    current_id: int | None = None
    current_lines: list[str] = []
    fallback_id = 1

    def flush() -> None:
        nonlocal current_id, current_lines, fallback_id
        text = _merge_lines(current_lines)
        if not text:
            current_id = None
            current_lines = []
            return
        entry_id = _next_entry_id(current_id, entries, fallback_id)
        fallback_id = max(fallback_id, entry_id + 1)
        entries.append(
            TrainingEntry(
                id=entry_id,
                text=text,
                status="parsed",
                source_index=len(entries) + 1,
            )
        )
        current_id = None
        current_lines = []

    for raw_line in raw_text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue

        pure_match = _PURE_NUMBER_RE.match(line)
        if pure_match:
            flush()
            current_id = int(pure_match.group(1))
            continue

        inline_match = _INLINE_NUMBER_RE.match(line)
        if inline_match:
            candidate_id = int(inline_match.group(1))
            content = inline_match.group(2).strip()
            if content:
                flush()
                current_id = candidate_id
                current_lines = [content]
                continue

        if _is_title_line(line):
            continue

        if current_id is None and not current_lines:
            current_id = fallback_id
        current_lines.append(line)

    flush()
    return renumber_entries(entries)


def renumber_entries(entries: Iterable[TrainingEntry], *, reset_generated: bool = False) -> list[TrainingEntry]:
    renumbered: list[TrainingEntry] = []
    for index, entry in enumerate(entries, start=1):
        item = TrainingEntry.from_dict(entry.to_dict())
        item.id = index
        item.source_index = index
        if reset_generated and item.audio_path:
            item.audio_path = None
            item.generated_at = None
            item.voice_provider = None
            item.status = "tagged" if item.category or item.length_type or item.emotion else "parsed"
            item.note = _append_note(item.note, "已重新编号。音频文件名需重新生成。")
        renumbered.append(item)
    return renumbered


def entries_to_numbered_text(entries: Iterable[TrainingEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        lines.append(str(entry.id))
        lines.append(entry.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_duplicate_counts(entries: Iterable[TrainingEntry]) -> Counter[str]:
    return Counter(_normalize_for_compare(entry.text) for entry in entries if entry.text.strip())


def _clean_line(line: str) -> str:
    return re.sub(r"[ \t]+", " ", line).strip()


def _is_title_line(line: str) -> bool:
    if _SEPARATOR_RE.match(line):
        return True
    if re.match(r"^(第[一二三四五六七八九十0-9]+[章节部分卷]|[A-Za-z0-9 _-]{1,32})$", line):
        return True
    return False


def _merge_lines(lines: list[str]) -> str:
    merged = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not merged:
            merged = stripped
            continue
        if _needs_space(merged[-1], stripped[0]):
            merged += " " + stripped
        else:
            merged += stripped
    return merged.strip()


def _needs_space(left: str, right: str) -> bool:
    return bool(re.match(r"[A-Za-z0-9]", left) and re.match(r"[A-Za-z0-9]", right))


def _next_entry_id(current_id: int | None, entries: list[TrainingEntry], fallback_id: int) -> int:
    if current_id is None:
        return fallback_id
    existing_ids = {entry.id for entry in entries}
    if current_id not in existing_ids:
        return current_id
    return max(max(existing_ids, default=0) + 1, fallback_id)


def _normalize_for_compare(text: str) -> str:
    return re.sub(r"\s+", "", text).strip().lower()


def _append_note(existing: str, addition: str) -> str:
    existing = existing.strip()
    addition = addition.strip()
    if not addition:
        return existing
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing} | {addition}"
