# Fairy Voice Runtime

The desktop host starts this worker as a hidden, loopback-only process. Development uses
the configured CUDA Python runtime; release builds package `fairy-voice-worker.exe` with
`scripts/build-voice-sidecar.ps1`.

The verified Windows runtime baseline is:

- Python 3.10.11
- PyTorch and torchaudio 2.7.0 with CUDA 12.8
- TensorRT 10.13.3.9 for the CosyVoice flow engine
- ONNX Runtime GPU 1.22.0 with `CUDAExecutionProvider` and no shadowing CPU distribution
- CosyVoice 3 model `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`

TensorRT 11 is not compatible with the current CosyVoice conversion adapter because it
removed the explicit-batch builder flag. The release builder verifies and packages the
compatible runtime; the model and generated GPU-specific TensorRT plan remain in the
per-user application data directory.

Run `scripts/check-voice-runtime.ps1` to validate the locked environment without loading
the voice model. A simultaneous `onnxruntime` CPU distribution is rejected because it can
shadow `onnxruntime-gpu` while still allowing imports to appear successful.

The Worker does not report `ready` until model loading, speaker registration, and a
production-shaped bidirectional streaming warmup have completed. The bundled prompt is
a 1.834-second, 16 kHz mono Fairy-owned legacy recording; keeping the zero-shot prefix
short is required for the 450ms first-frame latency gate.
