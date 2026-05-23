from __future__ import annotations

import itertools
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import voice_config

from .audio_player import AudioPlayer
from .fairy_tts import FairyTTS
from .sentence_buffer import SentenceBuffer
from .timestamps import StreamingTextAllocator, build_character_timestamps
from .voice_lines import VOICE_LINES

logger = logging.getLogger(__name__)


ERROR_PRIORITY = 0
SYSTEM_PRIORITY = 1
RESPONSE_PRIORITY = 10
NOTICE_PRIORITY = 20
_QUEUE_STOP = object()
_SYSTEM_EVENT_COOLDOWNS = {
    "thinking": 8.0,
    "processing": 3.0,
    "analyzing_image": 3.0,
    "searching": 3.0,
    "reading_webpage": 3.0,
    "analysis_complete": 2.0,
    "information_found": 2.0,
    "complete": 2.0,
}


@dataclass(order=True)
class SpeechTask:
    priority: int
    order: int
    text: str = field(compare=False)
    system_voice: bool = field(compare=False, default=False)


class FairyVoice:
    """Sentence-level streaming voice controller."""

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = voice_config.enabled if enabled is None else enabled
        self.on_playback_state_change: Callable[[bool], None] | None = None
        self.on_playback_event: Callable[[str], None] | None = None
        self.on_playback_progress: Callable[[dict[str, Any]], None] | None = None
        self._tts = FairyTTS()
        self._player = AudioPlayer(
            sample_rate=voice_config.audio_sample_rate,
            on_state_change=self._handle_playback_state_change,
            on_progress=self._handle_playback_progress,
        )
        self._tasks: queue.PriorityQueue[tuple[int, int, Any]] = queue.PriorityQueue()
        self._counter = itertools.count()
        self._buffer = SentenceBuffer(max_chars=voice_config.max_sentence_chars)
        self._buffer_lock = threading.Lock()
        self._thinking_timer: threading.Timer | None = None
        self._stream_has_spoken = False
        self._stream_spoken_sentences = 0
        self._stream_spoken_chars = 0
        self._last_enqueued_text = ""
        self._last_enqueued_at = 0.0
        self._last_system_spoken_at: dict[str, float] = {}
        self._warmup_started = False
        self._worker = threading.Thread(target=self._tts_loop, name="fairy-tts-worker", daemon=True)
        self._worker.start()

    def feed_token(self, token: str) -> None:
        if not self.enabled or not voice_config.speak_responses or not voice_config.stream_responses:
            return
        if not token:
            return
        with self._buffer_lock:
            sentences = self._buffer.add_token(token)
        for sentence in sentences:
            self._speak_stream_sentence(sentence)

    def speak(self, text: str, *, speak: bool = True, priority: int = RESPONSE_PRIORITY) -> None:
        if not self.enabled or not speak or not voice_config.speak_responses:
            return
        cleaned = text.strip()
        if len(cleaned) < voice_config.speak_min_chars:
            return

        buffer = SentenceBuffer(max_chars=voice_config.max_sentence_chars)
        spoken_sentences = 0
        spoken_chars = 0
        for chunk in buffer.add_token(cleaned):
            if voice_config.max_response_sentences > 0 and spoken_sentences >= voice_config.max_response_sentences:
                break
            if voice_config.max_response_chars > 0 and spoken_chars + len(chunk) > voice_config.max_response_chars:
                break
            self._enqueue_text(chunk, priority=priority, system_voice=False)
            spoken_sentences += 1
            spoken_chars += len(chunk)
        for tail in buffer.flush():
            if voice_config.max_response_sentences > 0 and spoken_sentences >= voice_config.max_response_sentences:
                break
            if voice_config.max_response_chars > 0 and spoken_chars + len(tail) > voice_config.max_response_chars:
                break
            self._enqueue_text(tail, priority=priority, system_voice=False)
            spoken_sentences += 1
            spoken_chars += len(tail)

    def system_line(self, event: str, *, priority: int = SYSTEM_PRIORITY) -> None:
        if not self.enabled or not voice_config.speak_system:
            return
        line = VOICE_LINES.get(event)
        if not line:
            return

        now = time.monotonic()
        cooldown = _SYSTEM_EVENT_COOLDOWNS.get(event, 2.0)
        last_spoken = self._last_system_spoken_at.get(event, 0.0)
        if (now - last_spoken) < cooldown:
            return
        self._last_system_spoken_at[event] = now
        self.speak_system_text(line, priority=priority)

    def speak_system_text(self, text: str, *, priority: int = SYSTEM_PRIORITY) -> None:
        if not self.enabled or not voice_config.speak_system:
            return
        for segment in self._prepare_system_segments(text):
            self._enqueue_text(segment, priority=priority, system_voice=True)

    def is_ready(self) -> bool:
        if not self.enabled:
            return True
        return self._tts.is_ready()

    def start_background_warmup(self) -> None:
        if not self.enabled or self._warmup_started or not voice_config.warmup_on_start:
            return
        self._warmup_started = True
        self._tts.start_warmup()

    def start_stream(self, *, thinking_delay_sec: float | None = None) -> None:
        self.cancel_stream()
        if not self.enabled:
            return
        delay = voice_config.thinking_notice_delay_sec if thinking_delay_sec is None else thinking_delay_sec
        self._thinking_timer = threading.Timer(delay, self._emit_thinking_notice)
        self._thinking_timer.daemon = True
        self._thinking_timer.start()

    def finish_stream(self) -> None:
        if not self.enabled:
            return
        self._cancel_thinking_notice()
        with self._buffer_lock:
            tails = self._buffer.flush()
        for tail in tails:
            self._speak_stream_sentence(tail)

    def cancel_stream(self) -> None:
        self._cancel_thinking_notice()
        self._stream_has_spoken = False
        self._stream_spoken_sentences = 0
        self._stream_spoken_chars = 0
        with self._buffer_lock:
            self._buffer.clear()
        self._emit_progress_clear()

    def interrupt(self) -> None:
        self.cancel_stream()
        self._clear_pending_speech()
        self._player.interrupt()

    def shutdown(self) -> None:
        self.cancel_stream()
        self._clear_pending_speech()
        self._tasks.put((-10**9, next(self._counter), _QUEUE_STOP))
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self._tts.shutdown()
        self._player.shutdown()

    def _speak_stream_sentence(self, sentence: str) -> None:
        sentence = sentence.strip()
        if len(sentence) < voice_config.speak_min_chars:
            return
        if not self._allow_stream_sentence(sentence):
            return
        self._stream_has_spoken = True
        self._cancel_thinking_notice()
        self._enqueue_text(sentence, priority=RESPONSE_PRIORITY, system_voice=False)

    def _emit_thinking_notice(self) -> None:
        if not voice_config.speak_thinking_notice:
            return
        if self._stream_has_spoken:
            return
        self.system_line("thinking", priority=SYSTEM_PRIORITY)

    def _cancel_thinking_notice(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.cancel()
            self._thinking_timer = None

    def _enqueue_text(self, text: str, *, priority: int, system_voice: bool) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        now = time.monotonic()
        if cleaned == self._last_enqueued_text and (now - self._last_enqueued_at) < 2.0:
            return
        self._last_enqueued_text = cleaned
        self._last_enqueued_at = now
        task = SpeechTask(
            priority=priority,
            order=next(self._counter),
            text=cleaned,
            system_voice=system_voice,
        )
        self._tasks.put((task.priority, task.order, task))

    def _prepare_system_segments(self, text: str) -> list[str]:
        cleaned = " ".join(str(text or "").replace("\r", "\n").split())
        if not cleaned:
            return []

        fragments: list[str] = []
        for block in re.split(r"[\n]+", str(text or "")):
            normalized_block = " ".join(block.split()).strip()
            if not normalized_block:
                continue
            fragments.extend(self._split_system_block(normalized_block))

        if fragments:
            return fragments
        return self._split_system_block(cleaned)

    def _split_system_block(self, text: str) -> list[str]:
        buffer = SentenceBuffer(max_chars=min(18, voice_config.max_sentence_chars))
        chunks: list[str] = []
        normalized = text.strip()
        if not normalized:
            return chunks
        normalized = re.sub(r"[，,]{2,}", "，", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        for chunk in buffer.add_token(normalized):
            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)
        for tail in buffer.flush():
            tail = tail.strip()
            if tail:
                chunks.append(tail)
        return chunks

    def _allow_stream_sentence(self, sentence: str) -> bool:
        if voice_config.max_response_sentences > 0 and self._stream_spoken_sentences >= voice_config.max_response_sentences:
            return False
        if voice_config.max_response_chars > 0 and self._stream_spoken_chars + len(sentence) > voice_config.max_response_chars:
            return False
        self._stream_spoken_sentences += 1
        self._stream_spoken_chars += len(sentence)
        return True

    def _tts_loop(self) -> None:
        while True:
            _, _, item = self._tasks.get()
            if item is _QUEUE_STOP:
                break
            task = item
            try:
                yielded = False
                allocator = StreamingTextAllocator(
                    task.text,
                    chars_per_second=voice_config.timestamp_chars_per_second,
                )
                for chunk in self._tts.stream_chunks(task.text, system_voice=task.system_voice):
                    yielded = True
                    chunk_text = allocator.consume(chunk.duration_sec, is_final=chunk.is_final)
                    if chunk.is_final and not chunk_text:
                        chunk_text = allocator.flush()
                    metadata = self._build_progress_metadata(chunk_text, chunk.duration_sec, task.system_voice)
                    self._player.enqueue(
                        chunk.path,
                        priority=task.priority,
                        ephemeral=True,
                        metadata=metadata,
                    )
                if not yielded:
                    continue
            except Exception as error:
                logger.exception(
                    "fairy_tts_task_failed system_voice=%s text=%r",
                    task.system_voice,
                    task.text[:120],
                )
                self._emit_voice_error(error, task)
                continue

    def _clear_pending_speech(self) -> None:
        while True:
            try:
                _, _, item = self._tasks.get_nowait()
            except queue.Empty:
                break
            if item is _QUEUE_STOP:
                self._tasks.put((-10**9, next(self._counter), _QUEUE_STOP))
                break

    def _handle_playback_state_change(self, active: bool) -> None:
        callback = self.on_playback_state_change
        if callback is None:
            pass
        else:
            try:
                callback(active)
            except Exception:
                pass

        event_callback = self.on_playback_event
        if event_callback is None:
            if not active:
                self._emit_progress_clear()
            return
        try:
            event_callback("voice_start" if active else "voice_end")
        except Exception:
            pass
        if not active:
            self._emit_progress_clear()

    def _handle_playback_progress(self, payload: dict[str, Any]) -> None:
        callback = self.on_playback_progress
        if callback is None:
            return

        current_sec = payload.get("current_sec")
        timings = payload.get("timings") or []
        char_index = -1
        if isinstance(current_sec, (int, float)) and timings:
            for idx, item in enumerate(timings):
                start = float(item.get("start", 0.0))
                end = float(item.get("end", start))
                if start <= float(current_sec) <= max(end, start):
                    char_index = idx
                    break
            if char_index < 0 and payload.get("final"):
                char_index = len(timings) - 1

        enriched = dict(payload)
        enriched["char_index"] = char_index
        try:
            callback(enriched)
        except Exception:
            pass

    def _emit_voice_error(self, error: Exception, task: SpeechTask) -> None:
        event_callback = self.on_playback_event
        if event_callback is not None:
            try:
                event_callback("voice_error")
            except Exception:
                pass

        progress_callback = self.on_playback_progress
        if progress_callback is None:
            return
        try:
            progress_callback(
                {
                    "event": "voice_error",
                    "text": task.text,
                    "system_voice": task.system_voice,
                    "error": str(error),
                }
            )
        except Exception:
            pass

    def _build_progress_metadata(self, text: str, duration_sec: float, system_voice: bool) -> dict[str, Any] | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        timings = [
            {"text": item.text, "start": item.start, "end": item.end}
            for item in build_character_timestamps(cleaned, duration_sec)
        ]
        return {
            "text": cleaned,
            "timings": timings,
            "duration_sec": duration_sec,
            "system_voice": system_voice,
        }

    def _emit_progress_clear(self) -> None:
        callback = self.on_playback_progress
        if callback is None:
            return
        try:
            callback({"text": "", "timings": [], "char_index": -1, "final": True})
        except Exception:
            pass
