from __future__ import annotations


def compress_web_text(text: str, max_chars: int = 2500) -> str:
    lines = text.splitlines()
    clean_lines: list[str] = []
    seen: set[str] = set()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        clean_lines.append(line)

    merged = "\n".join(clean_lines)
    return merged[:max_chars]
