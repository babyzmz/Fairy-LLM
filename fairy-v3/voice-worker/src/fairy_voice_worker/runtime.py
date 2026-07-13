from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

MODEL_REPOSITORY = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
MODEL_REQUIRED_FILES = (
    "campplus.onnx",
    "cosyvoice3.yaml",
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
    "flow.decoder.estimator.fp32.onnx",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "speech_tokenizer_v3.onnx",
)
SAMPLE_RATE = 24_000
INITIAL_TOKEN_HOP = 5


@dataclass(frozen=True, slots=True)
class VoiceWorkerHealth:
    status: str
    model_repository: str
    model_installed: bool
    model_ready: bool
    model_digest: str | None
    prompt_ready: bool
    cuda_available: bool
    tensorrt_available: bool
    backend: str | None
    device_name: str | None
    sample_rate: int
    error_code: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class VoiceRuntime(Protocol):
    @property
    def sample_rate(self) -> int: ...

    def health(self) -> VoiceWorkerHealth: ...

    def load(self) -> None: ...

    def synthesize(self, text: str, cancellation: threading.Event) -> Iterator[bytes]: ...


class CosyVoice3Runtime:
    def __init__(
        self,
        *,
        model_dir: Path,
        source_dir: Path,
        prompt_wav: Path,
        prompt_text: Path,
    ) -> None:
        self._model_dir = model_dir
        self._source_dir = source_dir
        self._prompt_wav = prompt_wav
        self._prompt_text = prompt_text
        self._model: Any | None = None
        self._ready = False
        self._error_code: str | None = None
        self._model_digest: str | None = None
        self._load_lock = threading.Lock()
        self._synthesis_lock = threading.Lock()

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE

    def health(self) -> VoiceWorkerHealth:
        model_installed = _model_files_ready(self._model_dir)
        model_ready = self._ready
        prompt_ready = self._prompt_wav.is_file() and self._prompt_text.is_file()
        cuda_available = False
        tensorrt_available = importlib.util.find_spec("tensorrt") is not None
        device_name: str | None = None
        try:
            torch = importlib.import_module("torch")
            cuda_available = bool(torch.cuda.is_available())
            if cuda_available:
                device_name = str(torch.cuda.get_device_name(0))
        except Exception:
            pass
        if model_ready:
            status = "ready"
            error_code = None
        elif self._error_code is not None:
            status = "error"
            error_code = self._error_code
        elif not model_installed:
            status = "model_missing"
            error_code = "VOICE_MODEL_MISSING"
        elif not prompt_ready:
            status = "prompt_missing"
            error_code = "VOICE_PROMPT_MISSING"
        elif not cuda_available:
            status = "cuda_unavailable"
            error_code = "VOICE_CUDA_UNAVAILABLE"
        elif not tensorrt_available:
            status = "acceleration_unavailable"
            error_code = "VOICE_TRT_UNAVAILABLE"
        else:
            status = "warming"
            error_code = None
        return VoiceWorkerHealth(
            status=status,
            model_repository=MODEL_REPOSITORY,
            model_installed=model_installed,
            model_ready=model_ready,
            model_digest=self._model_digest,
            prompt_ready=prompt_ready,
            cuda_available=cuda_available,
            tensorrt_available=tensorrt_available,
            backend="tensorrt" if model_ready else None,
            device_name=device_name,
            sample_rate=SAMPLE_RATE,
            error_code=error_code,
        )

    def load(self) -> None:
        with self._load_lock:
            if self._ready:
                return
            self._ready = False
            try:
                self._validate_assets()
                matcha = self._source_dir / "third_party" / "Matcha-TTS"
                for path in (self._source_dir, matcha):
                    path_text = str(path)
                    if path_text in sys.path:
                        sys.path.remove(path_text)
                    sys.path.insert(0, path_text)
                _install_frozen_runtime_guards()
                cosyvoice = importlib.import_module("cosyvoice.cli.cosyvoice")
                trt_fingerprint = self._prepare_trt_plan()
                model = cosyvoice.AutoModel(
                    model_dir=str(self._model_dir),
                    fp16=True,
                    load_trt=True,
                    trt_concurrent=1,
                )
                prompt_text = " ".join(self._prompt_text.read_text(encoding="utf-8-sig").split())
                if "<|endofprompt|>" not in prompt_text:
                    prompt_text = f"You are a helpful assistant.<|endofprompt|>{prompt_text}"
                if not model.add_zero_shot_spk(
                    prompt_text,
                    str(self._prompt_wav),
                    "fairy-v3",
                ):
                    raise RuntimeError("CosyVoice 3 rejected the Fairy prompt")
                self._model = model
                self._model_digest = model_manifest_digest(self._model_dir)
                self._write_trt_manifest(trt_fingerprint)
                self._prime()
                self._ready = True
                self._error_code = None
            except Exception:
                self._model = None
                self._model_digest = None
                self._ready = False
                self._error_code = "VOICE_WORKER_LOAD_FAILED"
                raise

    def _prepare_trt_plan(self) -> dict[str, object]:
        torch = importlib.import_module("torch")
        tensorrt = importlib.import_module("tensorrt")
        fingerprint = {
            "compute_capability": list(torch.cuda.get_device_capability(0)),
            "device_name": str(torch.cuda.get_device_name(0)),
            "tensorrt_version": str(tensorrt.__version__),
        }
        plan = self._model_dir / "flow.decoder.estimator.fp16.mygpu.plan"
        manifest = self._model_dir / "fairy-trt-manifest.json"
        if plan.is_file():
            try:
                recorded = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                recorded = None
            if recorded != fingerprint:
                plan.unlink()
        return fingerprint

    def _write_trt_manifest(self, fingerprint: dict[str, object]) -> None:
        (self._model_dir / "fairy-trt-manifest.json").write_text(
            json.dumps(fingerprint, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def synthesize(self, text: str, cancellation: threading.Event) -> Iterator[bytes]:
        if self._model is None:
            raise RuntimeError("voice model is not ready")
        normalized = normalize_spoken_text(text)
        if not normalized:
            raise ValueError("voice text is empty")
        with self._synthesis_lock:
            self._model.model.token_hop_len = INITIAL_TOKEN_HOP
            outputs = self._model.inference_zero_shot(
                normalized,
                "",
                "",
                zero_shot_spk_id="fairy-v3",
                stream=True,
            )
            for output in outputs:
                if cancellation.is_set():
                    return
                speech = output["tts_speech"]
                pcm = (
                    speech.detach()
                    .to(dtype=importlib.import_module("torch").float32)
                    .clamp(-1.0, 1.0)
                    .mul(32767.0)
                    .to(dtype=importlib.import_module("torch").int16)
                    .cpu()
                    .contiguous()
                    .numpy()
                    .tobytes()
                )
                if pcm:
                    yield pcm

    def _validate_assets(self) -> None:
        if not _model_files_ready(self._model_dir):
            raise FileNotFoundError("CosyVoice 3 model files are incomplete")
        if not self._source_dir.joinpath("cosyvoice", "cli", "cosyvoice.py").is_file():
            raise FileNotFoundError("CosyVoice source runtime is unavailable")
        if not self._prompt_wav.is_file() or not self._prompt_text.is_file():
            raise FileNotFoundError("Fairy voice prompt assets are unavailable")

    def _prime(self) -> None:
        assert self._model is not None
        self._model.model.token_hop_len = INITIAL_TOKEN_HOP
        output = self._model.inference_zero_shot(
            "Fairy 已准备好继续工作。",
            "",
            "",
            zero_shot_spk_id="fairy-v3",
            stream=True,
        )
        for _ in output:
            pass


def runtime_from_environment() -> CosyVoice3Runtime:
    data_dir = Path(os.environ["FAIRY_VOICE_DATA_DIR"]).resolve()
    frozen_root = getattr(sys, "_MEIPASS", None)
    source_dir = (
        Path(frozen_root).resolve()
        if frozen_root is not None
        else Path(os.environ["FAIRY_COSYVOICE_ROOT"]).resolve()
    )
    assets_dir = Path(os.environ["FAIRY_VOICE_ASSETS_DIR"]).resolve()
    return CosyVoice3Runtime(
        model_dir=data_dir / "models" / "Fun-CosyVoice3-0.5B-2512",
        source_dir=source_dir,
        prompt_wav=assets_dir / "fairy_clone_core.wav",
        prompt_text=assets_dir / "fairy_clone_core.txt",
    )


def install_model(model_dir: Path) -> str:
    from huggingface_hub import snapshot_download

    model_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_REPOSITORY,
        local_dir=str(model_dir),
        allow_patterns=list(MODEL_REQUIRED_FILES),
    )
    if not _model_files_ready(model_dir):
        raise RuntimeError("downloaded CosyVoice 3 model is incomplete")
    digest = model_manifest_digest(model_dir)
    manifest = {
        "model_repository": MODEL_REPOSITORY,
        "manifest_digest": digest,
        "verified_at_unix_ms": time.time_ns() // 1_000_000,
    }
    (model_dir / "fairy-model-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return digest


def model_manifest_digest(model_dir: Path) -> str:
    manifest: list[tuple[str, int, str]] = []
    for relative in MODEL_REQUIRED_FILES:
        path = model_dir / relative
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest.append((relative, path.stat().st_size, digest.hexdigest()))
    return hashlib.sha256(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("ascii")
    ).hexdigest()


def normalize_spoken_text(text: str) -> str:
    import re

    value = re.sub(r"```.*?```|`[^`]*`", " ", text, flags=re.DOTALL)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[*_#~<>|{}\[\]]+", " ", value)
    value = " ".join(value.split()).strip()
    if value and value[-1] not in ".!?\u3002\uff01\uff1f":
        value += "\u3002"
    return value


def _model_files_ready(model_dir: Path) -> bool:
    return all((model_dir / relative).is_file() for relative in MODEL_REQUIRED_FILES)


def _install_frozen_runtime_guards() -> None:
    modelscope_guard = ModuleType("modelscope")

    def reject_download(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("CosyVoice runtime model downloads are disabled")

    modelscope_guard.snapshot_download = reject_download  # type: ignore[attr-defined]
    sys.modules["modelscope"] = modelscope_guard

    typeguard_guard = ModuleType("typeguard")

    def typechecked(target: Any = None, **_kwargs: object) -> Any:  # noqa: ANN401
        if target is None:
            return lambda decorated: decorated
        return target

    typeguard_guard.typechecked = typechecked  # type: ignore[attr-defined]
    sys.modules["typeguard"] = typeguard_guard


__all__ = [
    "MODEL_REPOSITORY",
    "CosyVoice3Runtime",
    "VoiceRuntime",
    "VoiceWorkerHealth",
    "install_model",
    "model_manifest_digest",
    "normalize_spoken_text",
    "runtime_from_environment",
]
