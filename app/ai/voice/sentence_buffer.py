from __future__ import annotations


SENTENCE_ENDINGS = {"。", "！", "？", ".", "!", "?"}
SOFT_BREAKS = {"，", "、", "；", "：", ",", ";", ":"}


class SentenceBuffer:
    """Token buffer that emits sentence-sized chunks for low-latency TTS."""

    def __init__(self, max_chars: int = 40) -> None:
        self.max_chars = max(8, max_chars)
        self.buffer = ""

    def add_token(self, token: str) -> list[str]:
        if not token:
            return []
        self.buffer += token
        return self._drain_ready_sentences()

    def flush(self) -> list[str]:
        tail = self.buffer.strip()
        self.buffer = ""
        return [tail] if tail else []

    def clear(self) -> None:
        self.buffer = ""

    def _drain_ready_sentences(self) -> list[str]:
        ready: list[str] = []
        while True:
            sentence = self._pop_next_sentence()
            if not sentence:
                break
            ready.append(sentence)
        return ready

    def _pop_next_sentence(self) -> str | None:
        stripped = self.buffer.strip()
        if not stripped:
            self.buffer = ""
            return None

        for idx, char in enumerate(self.buffer):
            if char not in SENTENCE_ENDINGS:
                continue
            sentence = self.buffer[: idx + 1].strip()
            self.buffer = self.buffer[idx + 1 :]
            return sentence or None

        if len(stripped) < self.max_chars:
            return None

        split_at = -1
        for idx, char in enumerate(self.buffer):
            if char in SOFT_BREAKS and idx + 1 >= self.max_chars // 2:
                split_at = idx + 1
        if split_at <= 0:
            split_at = self.max_chars

        sentence = self.buffer[:split_at].strip()
        self.buffer = self.buffer[split_at:]
        return sentence or None
