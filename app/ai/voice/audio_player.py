from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


_QUEUE_STOP = object()
logger = logging.getLogger(__name__)


@dataclass(order=True)
class AudioJob:
    priority: int
    order: int
    path: Path = field(compare=False)
    ephemeral: bool = field(compare=False, default=True)
    metadata: dict[str, Any] | None = field(compare=False, default=None)


class AudioPlayer:
    """Queue-based playback with priority interruption and state callbacks."""

    def __init__(
        self,
        sample_rate: int = 24000,
        on_state_change: Callable[[bool], None] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.on_state_change = on_state_change
        self.on_progress = on_progress
        self._jobs: queue.PriorityQueue[tuple[int, int, Any]] = queue.PriorityQueue()
        self._counter = itertools.count()
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()
        self._ready = False
        self._backend = "unavailable"
        self._pygame = None
        self._state_lock = threading.Lock()
        self._current_priority: int | None = None
        self._speaking = False
        self._thread = threading.Thread(target=self._playback_loop, name="fairy-audio-player", daemon=True)
        self._thread.start()

    def enqueue(
        self,
        audio_path: str | Path,
        priority: int = 10,
        *,
        ephemeral: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        path = Path(audio_path)
        if not path.exists():
            return

        with self._state_lock:
            should_preempt = self._current_priority is not None and priority < self._current_priority
        if should_preempt:
            self._interrupt_event.set()
            self._stop_current()
            self._drop_lower_priority(priority)

        order = next(self._counter)
        self._jobs.put(
            (
                priority,
                order,
                AudioJob(priority=priority, order=order, path=path, ephemeral=ephemeral, metadata=metadata),
            )
        )

    def interrupt(self) -> None:
        self._interrupt_event.set()
        self._stop_current()
        self._clear_pending()
        self._interrupt_event.clear()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._interrupt_event.set()
        self._stop_current()
        self._clear_pending()
        self._jobs.put((-10**9, next(self._counter), _QUEUE_STOP))
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._quit_mixer()
        self._clear_pending()

    def _playback_loop(self) -> None:
        self._init_mixer()
        while not self._stop_event.is_set():
            try:
                _, _, item = self._jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is _QUEUE_STOP:
                break
            job = item
            if self._interrupt_event.is_set():
                self._cleanup_file(job.path, ephemeral=job.ephemeral)
                continue
            self._play_file(job)
            self._cleanup_file(job.path, ephemeral=job.ephemeral)

    def _init_mixer(self) -> None:
        try:
            import pygame

            pygame.mixer.pre_init(self.sample_rate, -16, 2, 1024)
            pygame.mixer.init()
            self._pygame = pygame
            self._backend = "pygame"
            self._ready = True
            logger.info("AudioPlayer initialized with pygame backend")
            return
        except Exception:
            logger.warning("AudioPlayer pygame backend unavailable; falling back to winsound")

        try:
            import winsound  # noqa: F401

            self._backend = "winsound"
            self._ready = True
            logger.info("AudioPlayer initialized with winsound backend")
        except Exception:
            self._backend = "unavailable"
            self._ready = False
            logger.exception("AudioPlayer failed to initialize any playback backend")

    def _play_file(self, job: AudioJob) -> None:
        if not self._ready:
            return
        if self._backend == "pygame" and self._pygame is not None:
            self._play_file_pygame(job)
            return
        if self._backend == "winsound":
            self._play_file_winsound(job)
            return

    def _play_file_pygame(self, job: AudioJob) -> None:
        with self._state_lock:
            self._current_priority = job.priority
        self._set_speaking(True)
        try:
            self._pygame.mixer.music.load(str(job.path))
            self._pygame.mixer.music.play()
            self._emit_progress(job, 0.0, final=False)
            while self._pygame.mixer.music.get_busy():
                if self._stop_event.is_set() or self._interrupt_event.is_set():
                    self._stop_current()
                    break
                current_sec = max(0.0, self._pygame.mixer.music.get_pos() / 1000.0)
                self._emit_progress(job, current_sec, final=False)
                time.sleep(0.04)
        except Exception:
            logger.exception("AudioPlayer pygame playback failed for %s", job.path)
            self._stop_current()
        finally:
            self._emit_progress(job, None, final=True)
            with self._state_lock:
                self._current_priority = None
            self._set_speaking(False)
            try:
                self._pygame.mixer.music.unload()
            except Exception:
                pass
            self._interrupt_event.clear()

    def _play_file_winsound(self, job: AudioJob) -> None:
        import winsound

        duration_sec = self._estimate_duration(job.path)
        with self._state_lock:
            self._current_priority = job.priority
        self._set_speaking(True)
        started_at = time.monotonic()
        try:
            winsound.PlaySound(str(job.path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self._emit_progress(job, 0.0, final=False)
            while True:
                if self._stop_event.is_set() or self._interrupt_event.is_set():
                    self._stop_current()
                    break
                elapsed = max(0.0, time.monotonic() - started_at)
                if duration_sec > 0 and elapsed >= duration_sec:
                    break
                self._emit_progress(job, elapsed, final=False)
                time.sleep(0.04)
        except Exception:
            logger.exception("AudioPlayer winsound playback failed for %s", job.path)
            self._stop_current()
        finally:
            self._emit_progress(job, None, final=True)
            with self._state_lock:
                self._current_priority = None
            self._set_speaking(False)
            self._interrupt_event.clear()

    def _emit_progress(self, job: AudioJob, current_sec: float | None, *, final: bool) -> None:
        callback = self.on_progress
        if callback is None or job.metadata is None:
            return
        payload = dict(job.metadata)
        payload["current_sec"] = current_sec
        payload["final"] = final
        try:
            callback(payload)
        except Exception:
            pass

    def _set_speaking(self, active: bool) -> None:
        if self._speaking == active:
            return
        self._speaking = active
        callback = self.on_state_change
        if callback is None:
            return
        try:
            callback(active)
        except Exception:
            pass

    def _stop_current(self) -> None:
        if not self._ready:
            return
        if self._backend == "pygame" and self._pygame is not None:
            try:
                self._pygame.mixer.music.stop()
            except Exception:
                logger.exception("AudioPlayer failed to stop pygame playback")
            return
        if self._backend == "winsound":
            try:
                import winsound

                winsound.PlaySound(None, 0)
            except Exception:
                logger.exception("AudioPlayer failed to stop winsound playback")

    def _drop_lower_priority(self, threshold: int) -> None:
        retained: list[tuple[int, int, Any]] = []
        while True:
            try:
                item = self._jobs.get_nowait()
            except queue.Empty:
                break
            if item[2] is _QUEUE_STOP:
                retained.append(item)
                continue
            priority, _, job = item
            if priority <= threshold:
                retained.append(item)
            else:
                self._cleanup_file(job.path, ephemeral=job.ephemeral)
        for item in retained:
            self._jobs.put(item)

    def _clear_pending(self) -> None:
        while True:
            try:
                _, _, item = self._jobs.get_nowait()
            except queue.Empty:
                break
            if item is _QUEUE_STOP:
                self._jobs.put((-10**9, next(self._counter), _QUEUE_STOP))
                break
            self._cleanup_file(item.path, ephemeral=item.ephemeral)

    def _cleanup_file(self, path: Path, *, ephemeral: bool) -> None:
        if not ephemeral:
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def _quit_mixer(self) -> None:
        if self._backend != "pygame" or self._pygame is None:
            return
        try:
            self._pygame.mixer.quit()
        except Exception:
            logger.exception("AudioPlayer failed to quit pygame mixer")

    def _estimate_duration(self, path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as handle:
                frame_rate = handle.getframerate() or self.sample_rate
                frame_count = handle.getnframes()
            return frame_count / max(frame_rate, 1)
        except Exception:
            logger.exception("AudioPlayer failed to estimate duration for %s", path)
            return 0.0
