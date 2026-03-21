from __future__ import annotations

from app.ai.voice.fairy_tts import FairyTTS


def main() -> None:
    tts = FairyTTS()
    tts.warmup()
    for index, chunk_path in enumerate(tts.stream_to_files("主人，我正在分析数据。")):
        print(f"audio chunk received: {index} -> {chunk_path}")


if __name__ == "__main__":
    main()
