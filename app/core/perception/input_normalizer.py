from __future__ import annotations

import re


class InputNormalizer:
    _LEADING_FILLERS = (
        "please ",
        "can you ",
        "could you ",
        "would you ",
        "\u8bf7\u5e2e\u6211",  # 请帮我
        "\u5e2e\u6211",  # 帮我
        "\u8bf7",  # 请
        "\u9ebb\u70e6",  # 麻烦
        "\u7ed9\u6211",  # 给我
    )

    _TRAILING_PUNCTUATION_RE = re.compile(r"[\u3002\uff0c\uff01\uff1f,.!?]+$")

    def normalize(self, raw_text: str) -> str:
        text = " ".join(str(raw_text or "").strip().split())
        lowered = text.lower()
        for prefix in self._LEADING_FILLERS:
            prefix_lower = prefix.lower()
            if lowered.startswith(prefix_lower):
                text = text[len(prefix) :].strip()
                lowered = text.lower()
                break
        text = re.sub(r"\s+", " ", text)
        text = self._TRAILING_PUNCTUATION_RE.sub("", text)
        return text.strip()
