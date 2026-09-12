"""Single-use offline CPU worker. Input/audio stays in RAM; exit releases model."""

import base64
import io
import json
import os
import sys

MAX_BYTES = 20 * 1024 * 1024
MAX_SAMPLES = 120 * 16000


def decode_bounded(content):
    import av
    import numpy as np

    samples = []
    count = 0
    with av.open(io.BytesIO(content)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for output in resampler.resample(frame):
                count += output.samples
                if count > MAX_SAMPLES:
                    raise ValueError("recording duration exceeds limit")
                samples.append(output.to_ndarray().flatten())
        for output in resampler.resample(None):
            count += output.samples
            if count > MAX_SAMPLES:
                raise ValueError("recording duration exceeds limit")
            samples.append(output.to_ndarray().flatten())
    if not samples:
        raise ValueError("empty recording")
    return np.concatenate(samples).astype(np.float32) / 32768.0


def main():
    # Offline mode is enforced here as well as by the host; never download while
    # processing a microphone recording, and never load repository Python code.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    payload = json.loads(sys.stdin.buffer.read(28_000_001))
    content = base64.b64decode(payload["audio_base64"], validate=True)
    if not content or len(content) > MAX_BYTES:
        raise ValueError("recording size exceeds limit")
    audio = decode_bounded(content)
    from faster_whisper import WhisperModel

    model = WhisperModel(
        sys.argv[1],
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        num_workers=1,
        local_files_only=True,
    )
    segments, info = model.transcribe(
        audio,
        language=payload.get("language"),
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    text = "".join(segment.text for segment in segments).strip()
    if len(text) > 100_000:
        raise ValueError("transcript too large")
    print(json.dumps({"text": text, "language": info.language} if text else {"error": "NO_SPEECH"}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not log input, transcript, environment, or third-party exception text.
        sys.exit(1)
