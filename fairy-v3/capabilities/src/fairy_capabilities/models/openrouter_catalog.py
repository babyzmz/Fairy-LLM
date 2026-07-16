from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST,
    MODEL_ALLOWLIST_BY_ID,
    ModelAvailability,
    ModelCatalogEntry,
    ModelEndpointKind,
    ModelPrice,
    ProviderCredentialStatus,
)
from fairy_core.model_catalog.ports import (
    ModelCatalogFetchResult,
    ModelCatalogSourceError,
)
from fairy_core.providers import SecretValue

MAX_CATALOG_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CATALOG_RECORDS = 4_096


class OpenRouterCatalogSource:
    def __init__(
        self,
        *,
        base_url: str,
        secret: SecretValue | None,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        parsed = urlsplit(normalized_url)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
            raise ValueError("OpenRouter catalog base URL must use the official HTTPS endpoint")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("OpenRouter catalog timeout must be between 0 and 60 seconds")
        self._base_url = normalized_url
        self._secret = secret
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_seconds,
        )

    def fetch(self) -> ModelCatalogFetchResult:
        if self._secret is None:
            raise ModelCatalogSourceError(
                "CREDENTIAL_NOT_CONFIGURED",
                credential_status=ProviderCredentialStatus.UNAVAILABLE,
            )
        self._validate_credential(self._get_json("/key"))
        text_models = self._model_map(self._get_json("/models?output_modalities=text"))
        image_models = self._model_map(self._get_json("/images/models"))
        audio_models = self._model_map(self._get_json("/models?output_modalities=audio"))
        video_models = self._model_map(self._get_json("/videos/models"))

        records_by_endpoint = {
            ModelEndpointKind.CHAT: text_models,
            ModelEndpointKind.IMAGES: image_models,
            ModelEndpointKind.AUDIO: audio_models,
            ModelEndpointKind.VIDEOS: video_models,
        }
        entries: list[ModelCatalogEntry] = []
        for allowed in MODEL_ALLOWLIST:
            record = records_by_endpoint[allowed.endpoint_kind].get(allowed.model_id)
            if record is None:
                continue
            prices = self._prices(allowed.endpoint_kind, record)
            entries.append(_entry(allowed.model_id, record, prices=prices))
        return ModelCatalogFetchResult(
            entries=tuple(entries),
            credential_status=ProviderCredentialStatus.CONFIGURED,
            fetched_at=datetime.now(UTC),
        )

    @staticmethod
    def _validate_credential(payload: Mapping[str, Any]) -> None:
        if not isinstance(payload.get("data"), dict):
            raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_INVALID")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _prices(
        self,
        endpoint_kind: ModelEndpointKind,
        record: Mapping[str, Any],
    ) -> tuple[ModelPrice, ...]:
        if endpoint_kind is ModelEndpointKind.IMAGES:
            endpoints_path = record.get("endpoints")
            if isinstance(endpoints_path, str) and endpoints_path.startswith("/api/v1/images/"):
                endpoint_records = self._image_endpoint_records(self._get_json(endpoints_path))
                return _image_prices(endpoint_records)
        if endpoint_kind is ModelEndpointKind.VIDEOS:
            return _video_prices(record.get("pricing_skus"))
        return _general_prices(record.get("pricing"))

    def _get_json(self, path: str) -> Mapping[str, Any]:
        try:
            with self._client.stream(
                "GET",
                self._url(path),
                headers=self._headers(),
                timeout=self._timeout_seconds,
            ) as response:
                if response.status_code in {401, 403}:
                    raise ModelCatalogSourceError(
                        "CREDENTIAL_INVALID",
                        credential_status=ProviderCredentialStatus.INVALID,
                    )
                if response.status_code == 429:
                    raise ModelCatalogSourceError("MODEL_CATALOG_RATE_LIMITED")
                if not 200 <= response.status_code < 300:
                    raise ModelCatalogSourceError("MODEL_CATALOG_UPSTREAM_ERROR")
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as error:
                        raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_INVALID") from error
                    if declared_length > MAX_CATALOG_RESPONSE_BYTES:
                        raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_TOO_LARGE")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_CATALOG_RESPONSE_BYTES:
                        raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_TOO_LARGE")
        except httpx.TimeoutException as error:
            raise ModelCatalogSourceError("MODEL_CATALOG_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise ModelCatalogSourceError("MODEL_CATALOG_NETWORK_ERROR") from error
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_INVALID") from error
        if not isinstance(payload, dict):
            raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_INVALID")
        return payload

    def _model_map(self, payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for record in self._data(payload):
            model_id = record.get("id")
            if isinstance(model_id, str) and model_id in MODEL_ALLOWLIST_BY_ID:
                result[model_id] = record
        return result

    @staticmethod
    def _data(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        return OpenRouterCatalogSource._records(payload, "data")

    @staticmethod
    def _image_endpoint_records(
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ...]:
        field = "data" if "data" in payload else "endpoints"
        return OpenRouterCatalogSource._records(payload, field)

    @staticmethod
    def _records(payload: Mapping[str, Any], field: str) -> tuple[Mapping[str, Any], ...]:
        raw_records = payload.get(field)
        if not isinstance(raw_records, list) or not all(
            isinstance(record, dict) for record in raw_records
        ):
            raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_INVALID")
        if len(raw_records) > MAX_CATALOG_RECORDS:
            raise ModelCatalogSourceError("MODEL_CATALOG_RESPONSE_TOO_LARGE")
        return tuple(raw_records)

    def _url(self, path: str) -> str:
        if path.startswith("/api/v1/"):
            url = urljoin("https://openrouter.ai", path)
        else:
            url = f"{self._base_url}/{path.lstrip('/')}"
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
            raise ModelCatalogSourceError("MODEL_CATALOG_DESTINATION_BLOCKED")
        return url

    def _headers(self) -> dict[str, str]:
        assert self._secret is not None
        return {
            "Authorization": f"Bearer {self._secret.reveal()}",
            "Accept": "application/json",
            "HTTP-Referer": "https://fairy.local",
            "X-Title": "Fairy",
        }


def _entry(
    model_id: str,
    record: Mapping[str, Any],
    *,
    prices: tuple[ModelPrice, ...],
) -> ModelCatalogEntry:
    allowed = MODEL_ALLOWLIST_BY_ID[model_id]
    architecture = record.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    parameters = record.get("supported_parameters")
    parameter_names = (
        frozenset(value for value in parameters if isinstance(value, str))
        if isinstance(parameters, list)
        else frozenset(parameters)
        if isinstance(parameters, dict)
        else frozenset()
    )
    top_provider = record.get("top_provider")
    top_provider = top_provider if isinstance(top_provider, dict) else {}
    resolutions = _string_tuple(record.get("supported_resolutions"))
    aspect_ratios = _string_tuple(record.get("supported_aspect_ratios"))
    if isinstance(parameters, dict):
        resolutions = resolutions or _descriptor_values(parameters.get("resolution"))
        aspect_ratios = aspect_ratios or _descriptor_values(parameters.get("aspect_ratio"))
    return ModelCatalogEntry(
        model_id=allowed.model_id,
        display_name=allowed.display_name,
        category=allowed.category,
        endpoint_kind=allowed.endpoint_kind,
        description=allowed.description,
        paid=allowed.paid,
        availability=ModelAvailability.AVAILABLE,
        input_modalities=_string_tuple(architecture.get("input_modalities")),
        output_modalities=_string_tuple(architecture.get("output_modalities")),
        context_length=_positive_int(record.get("context_length")),
        max_output_tokens=_positive_int(top_provider.get("max_completion_tokens")),
        supports_tools="tools" in parameter_names or "tool_choice" in parameter_names,
        supports_structured_output=(
            "structured_outputs" in parameter_names or "response_format" in parameter_names
        ),
        supports_streaming=bool(record.get("supports_streaming", True)),
        supported_resolutions=resolutions,
        supported_aspect_ratios=aspect_ratios,
        prices=prices,
    )


def _general_prices(value: object) -> tuple[ModelPrice, ...]:
    if not isinstance(value, dict):
        return ()
    prices: list[ModelPrice] = []
    for billable, raw_cost in sorted(value.items()):
        cost = _cost(raw_cost)
        if not isinstance(billable, str) or cost is None:
            continue
        unit = "token" if billable in {"prompt", "completion"} else "request"
        prices.append(ModelPrice(billable=billable, unit=unit, cost_usd=cost))
    return tuple(prices)


def _image_prices(records: tuple[Mapping[str, Any], ...]) -> tuple[ModelPrice, ...]:
    result: dict[tuple[str, str, str | None], ModelPrice] = {}
    for record in records:
        raw_prices = record.get("pricing")
        if not isinstance(raw_prices, list):
            continue
        for raw_price in raw_prices:
            if not isinstance(raw_price, dict):
                continue
            billable = raw_price.get("billable")
            unit = raw_price.get("unit")
            cost = _cost(raw_price.get("cost_usd"))
            variant = raw_price.get("variant")
            if not isinstance(billable, str) or not isinstance(unit, str) or cost is None:
                continue
            normalized_variant = variant if isinstance(variant, str) and variant else None
            result[(billable, unit, normalized_variant)] = ModelPrice(
                billable=billable,
                unit=unit,
                cost_usd=cost,
                variant=normalized_variant,
            )
    ordered = sorted(result, key=lambda item: tuple(value or "" for value in item))
    return tuple(result[key] for key in ordered)


def _video_prices(value: object) -> tuple[ModelPrice, ...]:
    if not isinstance(value, dict):
        return ()
    result: list[ModelPrice] = []
    for billable, raw_cost in sorted(value.items()):
        cost = _cost(raw_cost)
        if not isinstance(billable, str) or cost is None:
            continue
        unit = "second" if "second" in billable else "request"
        result.append(ModelPrice(billable=billable, unit=unit, cost_usd=cost))
    return tuple(result)


def _cost(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    normalized = format(amount, "f").rstrip("0").rstrip(".")
    return normalized or "0"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _descriptor_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return _string_tuple(value.get("values"))


__all__ = ["MAX_CATALOG_RECORDS", "MAX_CATALOG_RESPONSE_BYTES", "OpenRouterCatalogSource"]
