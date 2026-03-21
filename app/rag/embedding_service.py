from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from app.rag.rag_schema import RAGSettings
from app.settings import SecretStore


logger = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}|\d+(?:\.\d+)?")


class BaseEmbeddingService(ABC):
    provider_name: str = "base"
    model_id: str = "base"
    dimension_hint: int = 0

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashEmbeddingService(BaseEmbeddingService):
    provider_name = "hash"

    def __init__(self, dimension: int = 192) -> None:
        self.dimension = dimension
        self.dimension_hint = dimension
        self.model_id = "hash-v1"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = TOKEN_PATTERN.findall(text.lower())
        if not tokens:
            return vector
        for token in tokens:
            bucket = int(hashlib.sha1(token.encode("utf-8")).hexdigest(), 16) % self.dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class QwenCloudEmbeddingService(BaseEmbeddingService):
    provider_name = "qwen_cloud"

    def __init__(self, *, base_url: str, model: str, api_key: str, timeout_seconds: int = 25) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = model.strip()
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        if not self.base_url or not self.model_id or not self.api_key:
            raise ValueError("Qwen cloud embedding config is incomplete.")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = requests.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_id,
                "input": texts,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError("Embedding response missing data.")
        embeddings: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise ValueError("Embedding response format is invalid.")
            embeddings.append([float(value) for value in item["embedding"]])
        return embeddings


class LocalEmbeddingService(BaseEmbeddingService):
    provider_name = "bge_local"

    def __init__(
        self,
        *,
        model_path: str,
        model: str,
        backend: str = "sentence_transformers",
        device: str = "cpu",
        batch_size: int = 16,
        threads: int = 4,
    ) -> None:
        self.model_path = model_path.strip()
        self.model_id = model.strip() or Path(self.model_path).name
        self.backend = backend.strip() or "sentence_transformers"
        self.device = device.strip() or "cpu"
        self.batch_size = max(1, int(batch_size))
        self.threads = max(1, int(threads))
        self._model = None
        if not self.model_path:
            raise ValueError("Local embedding model path is missing.")

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self.backend != "sentence_transformers":
            raise ValueError(f"Unsupported local embedding backend: {self.backend}")
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("sentence_transformers is required for local embeddings") from exc

        try:
            import torch

            torch.set_num_threads(self.threads)
        except Exception:
            pass

        self._model = SentenceTransformer(self.model_path, device=self.device)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        matrix = model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        rows = matrix.tolist() if hasattr(matrix, "tolist") else matrix
        return [[float(value) for value in row] for row in rows]


Qwen3EmbeddingService = QwenCloudEmbeddingService


@dataclass(slots=True)
class EmbeddingRuntime:
    service: BaseEmbeddingService
    provider_name: str
    model_id: str
    dimension: int = 0
    fingerprint: str = ""
    collection_name: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""


class EmbeddingServiceFactory:
    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store or SecretStore()

    def create(self, settings: RAGSettings) -> EmbeddingRuntime:
        if not settings.embedding_enabled:
            return self._hash_runtime("embedding_disabled")

        provider = self._normalize_provider(settings.embedding_provider)
        if provider == "hash":
            return EmbeddingRuntime(service=HashEmbeddingService(), provider_name="hash", model_id="hash-v1")

        if provider == "qwen_cloud":
            api_key = self.secret_store.get(settings.embedding_api_key_ref)
            if not api_key:
                logger.warning("Qwen cloud embedding API key is missing. Falling back to hash embedding.")
                return self._hash_runtime("missing_api_key")
            try:
                service = QwenCloudEmbeddingService(
                    base_url=settings.embedding_base_url,
                    model=settings.embedding_model,
                    api_key=api_key,
                    timeout_seconds=settings.embedding_timeout_seconds,
                )
                return EmbeddingRuntime(service=service, provider_name=provider, model_id=service.model_id)
            except Exception as exc:
                logger.warning("Failed to initialize qwen cloud embedding provider: %s", exc)
                return self._hash_runtime(str(exc))

        if provider == "bge_local":
            try:
                service = LocalEmbeddingService(
                    model_path=settings.embedding_model_path,
                    model=settings.embedding_model,
                    backend=settings.embedding_backend,
                    device=settings.embedding_device,
                    batch_size=settings.embedding_batch_size,
                    threads=settings.embedding_threads,
                )
                return EmbeddingRuntime(service=service, provider_name=provider, model_id=service.model_id)
            except Exception as exc:
                logger.warning("Failed to initialize local embedding provider: %s", exc)
                return self._hash_runtime(str(exc))

        logger.warning("Unknown embedding provider '%s'. Falling back to hash.", provider)
        return self._hash_runtime("unknown_provider")

    def create_from_config(self, config: dict[str, Any]) -> EmbeddingRuntime:
        return self.create(RAGSettings.from_dict(config))

    def _normalize_provider(self, provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized == "qwen3":
            return "qwen_cloud"
        return normalized or "qwen_cloud"

    def _hash_runtime(self, reason: str) -> EmbeddingRuntime:
        return EmbeddingRuntime(
            service=HashEmbeddingService(),
            provider_name="hash",
            model_id="hash-v1",
            fallback_used=True,
            fallback_reason=reason,
        )
