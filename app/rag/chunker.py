from __future__ import annotations

import math
import re
import uuid

from app.rag.rag_schema import ChunkMetadata, ChunkRecord


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 1.6))


class TextChunker:
    def __init__(self, *, target_chars: int = 800, hard_limit_chars: int = 1100) -> None:
        self.target_chars = target_chars
        self.hard_limit_chars = hard_limit_chars

    def chunk_text(
        self,
        *,
        text: str,
        source_kind: str,
        source_ref_id: str,
        title: str = "",
        tags: list[str] | None = None,
        created_at: str = "",
        session_id: str = "",
        importance: float = 0.0,
        document_id: str = "",
    ) -> list[ChunkRecord]:
        normalized = self._normalize_text(text)
        if not normalized:
            return []
        parts = self._split_into_parts(normalized)
        chunks: list[ChunkRecord] = []
        for index, part in enumerate(parts):
            metadata = ChunkMetadata(
                source_kind=source_kind,
                source_ref_id=source_ref_id,
                title=title,
                tags=list(tags or []),
                created_at=created_at,
                session_id=session_id,
                importance=float(importance),
                document_id=document_id,
            )
            chunks.append(
                ChunkRecord(
                    id=f"chunk_{uuid.uuid4().hex[:16]}",
                    content=part,
                    chunk_index=index,
                    token_estimate=estimate_tokens(part),
                    metadata=metadata,
                )
            )
        return chunks

    def _normalize_text(self, text: str) -> str:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return re.sub(r"\n{3,}", "\n\n", cleaned)

    def _split_into_parts(self, text: str) -> list[str]:
        if len(text) <= self.target_chars:
            return [text]
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        if not paragraphs:
            paragraphs = [text]

        parts: list[str] = []
        buffer = ""
        for paragraph in paragraphs:
            candidate = paragraph if not buffer else f"{buffer}\n\n{paragraph}"
            if len(candidate) <= self.target_chars:
                buffer = candidate
                continue
            if buffer:
                parts.append(buffer)
                buffer = ""
            if len(paragraph) <= self.hard_limit_chars:
                buffer = paragraph
                continue
            parts.extend(self._split_long_paragraph(paragraph))
        if buffer:
            parts.append(buffer)
        return parts

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", paragraph) if part.strip()]
        if not sentences:
            sentences = [paragraph]
        parts: list[str] = []
        buffer = ""
        for sentence in sentences:
            candidate = sentence if not buffer else buffer + sentence
            if len(candidate) <= self.target_chars:
                buffer = candidate
                continue
            if buffer:
                parts.append(buffer)
            buffer = sentence
        if buffer:
            parts.append(buffer)
        return parts
