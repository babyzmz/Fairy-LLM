from __future__ import annotations

import argparse
import http.client
import json
import threading
import time
from dataclasses import asdict, dataclass
from math import ceil
from uuid import uuid4


@dataclass(frozen=True)
class StreamMeasurement:
    first_frame_ms: float
    generation_ms: float
    pcm_bytes: int
    audio_ms: float


def register_token(host: str, port: int, bootstrap: str) -> str:
    token = uuid4().hex + uuid4().hex
    status, _ = request_json(
        host,
        port,
        "POST",
        "/v1/tokens",
        bootstrap,
        {"token": token},
    )
    if status != 201:
        raise RuntimeError(f"token registration failed: HTTP {status}")
    return token


def measure_stream(
    host: str,
    port: int,
    bootstrap: str,
    text: str,
    *,
    first_frame: threading.Event | None = None,
    session_id: str | None = None,
) -> StreamMeasurement:
    token = register_token(host, port, bootstrap)
    session_id = session_id or str(uuid4())
    body = json.dumps(
        {
            "session_id": session_id,
            "scope_digest": "0" * 64,
            "text": text,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    connection = http.client.HTTPConnection(host, port, timeout=600)
    started = time.perf_counter()
    connection.request(
        "POST",
        "/v1/sessions",
        body=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = connection.getresponse()
    if response.status != 200:
        raise RuntimeError(f"voice stream failed: HTTP {response.status}")
    first = response.read(4096)
    first_at = time.perf_counter()
    if first_frame is not None:
        first_frame.set()
    remaining = response.read()
    completed = time.perf_counter()
    connection.close()
    pcm_bytes = len(first) + len(remaining)
    if pcm_bytes == 0:
        raise RuntimeError("voice stream returned no PCM frames")
    return StreamMeasurement(
        first_frame_ms=(first_at - started) * 1000,
        generation_ms=(completed - started) * 1000,
        pcm_bytes=pcm_bytes,
        audio_ms=pcm_bytes / (24_000 * 2) * 1000,
    )


def request_json(
    host: str,
    port: int,
    method: str,
    path: str,
    token: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection(host, port, timeout=10)
    connection.request(
        method,
        path,
        body=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = connection.getresponse()
    content = response.read()
    connection.close()
    return response.status, json.loads(content or b"{}")


def measure_cancel(host: str, port: int, bootstrap: str) -> float:
    session_id = str(uuid4())
    first_frame = threading.Event()
    worker = threading.Thread(
        target=measure_stream,
        args=(
            host,
            port,
            bootstrap,
            "Fairy is reading a deliberately long sentence so cancellation can be "
            "measured immediately and reliably without waiting for all generated audio "
            "to finish.",
        ),
        kwargs={"first_frame": first_frame, "session_id": session_id},
        daemon=True,
    )
    worker.start()
    if not first_frame.wait(timeout=60):
        raise RuntimeError("voice stream produced no first frame")
    token = register_token(host, port, bootstrap)
    started = time.perf_counter()
    status, _ = request_json(
        host,
        port,
        "DELETE",
        f"/v1/sessions/{session_id}",
        token,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if status != 200:
        raise RuntimeError(f"voice cancellation failed: HTTP {status}")
    worker.join(timeout=30)
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bootstrap", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.samples <= 20:
        raise SystemExit("--samples must be between 1 and 20")
    first_samples = [
        measure_stream(
            args.host,
            args.port,
            args.bootstrap,
            "你好，我是 Fairy，很高兴继续陪你完成今天的工作。",  # noqa: RUF001
        )
        for _ in range(args.samples)
    ]
    first = first_samples[-1]
    second = measure_stream(
        args.host,
        args.port,
        args.bootstrap,
        "下一段语音会在前一段播放时提前生成。",
    )
    estimated_gap = max(
        0.0,
        first.generation_ms + second.first_frame_ms - first.first_frame_ms - first.audio_ms,
    )
    cancel_ms = measure_cancel(args.host, args.port, args.bootstrap)
    result = {
        "first": asdict(first),
        "first_frame_p95_ms": percentile95(
            [measurement.first_frame_ms for measurement in first_samples]
        ),
        "first_frame_samples_ms": [measurement.first_frame_ms for measurement in first_samples],
        "second": asdict(second),
        "estimated_prefetched_gap_ms": estimated_gap,
        "cancel_response_ms": cancel_ms,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.enforce and (
        result["first_frame_p95_ms"] > 450 or estimated_gap >= 80 or cancel_ms > 100
    ):
        raise SystemExit(1)


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * 0.95) - 1)]


if __name__ == "__main__":
    main()
