from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType


class ModelEndpointKind(StrEnum):
    CHAT = "chat"
    IMAGES = "images"
    AUDIO = "audio"
    VIDEOS = "videos"


class ModelCategory(StrEnum):
    PRIMARY = "primary"
    STRONGEST = "strongest"
    CODE = "code"
    IMAGE = "image"
    MUSIC = "music"
    VIDEO = "video"
    FREE_GENERAL = "free_general"
    FREE_CODE = "free_code"


class ModelAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderCredentialStatus(StrEnum):
    CONFIGURED = "configured"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class ModelSelectionMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class AllowedModel:
    model_id: str
    display_name: str
    category: ModelCategory
    endpoint_kind: ModelEndpointKind
    description: str
    paid: bool


MODEL_ALLOWLIST = (
    AllowedModel(
        model_id="deepseek/deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        category=ModelCategory.PRIMARY,
        endpoint_kind=ModelEndpointKind.CHAT,
        description="Default coordinator and general-purpose model",
        paid=True,
    ),
    AllowedModel(
        model_id="z-ai/glm-5.2",
        display_name="GLM 5.2",
        category=ModelCategory.STRONGEST,
        endpoint_kind=ModelEndpointKind.CHAT,
        description="Complex reasoning, long context, and verification",
        paid=True,
    ),
    AllowedModel(
        model_id="moonshotai/kimi-k2.7-code",
        display_name="Kimi K2.7 Code",
        category=ModelCategory.CODE,
        endpoint_kind=ModelEndpointKind.CHAT,
        description="Code implementation and repository-scale changes",
        paid=True,
    ),
    AllowedModel(
        model_id="google/gemini-3.1-flash-lite-image",
        display_name="Gemini 3.1 Flash Lite Image",
        category=ModelCategory.IMAGE,
        endpoint_kind=ModelEndpointKind.IMAGES,
        description="Image generation and image editing",
        paid=True,
    ),
    AllowedModel(
        model_id="google/lyria-3-pro-preview",
        display_name="Lyria 3 Pro Preview",
        category=ModelCategory.MUSIC,
        endpoint_kind=ModelEndpointKind.AUDIO,
        description="Music and full-song generation; not Fairy speech",
        paid=True,
    ),
    AllowedModel(
        model_id="bytedance/seedance-2.0",
        display_name="Seedance 2.0",
        category=ModelCategory.VIDEO,
        endpoint_kind=ModelEndpointKind.VIDEOS,
        description="Asynchronous video generation",
        paid=True,
    ),
    AllowedModel(
        model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
        display_name="Nemotron 3 Ultra",
        category=ModelCategory.FREE_GENERAL,
        endpoint_kind=ModelEndpointKind.CHAT,
        description="Free general-purpose model",
        paid=False,
    ),
    AllowedModel(
        model_id="qwen/qwen3-coder:free",
        display_name="Qwen3 Coder",
        category=ModelCategory.FREE_CODE,
        endpoint_kind=ModelEndpointKind.CHAT,
        description="Free coding model",
        paid=False,
    ),
)
MODEL_ALLOWLIST_BY_ID = MappingProxyType({model.model_id: model for model in MODEL_ALLOWLIST})


@dataclass(frozen=True, slots=True)
class ModelPrice:
    billable: str
    unit: str
    cost_usd: str
    variant: str | None = None

    def __post_init__(self) -> None:
        for name, value in (("billable", self.billable), ("unit", self.unit)):
            if not value or value != value.strip() or len(value) > 128:
                raise ValueError(f"model price {name} is invalid")
        try:
            amount = Decimal(self.cost_usd)
        except InvalidOperation as error:
            raise ValueError("model price cost_usd is invalid") from error
        if not amount.is_finite() or amount < 0:
            raise ValueError("model price cost_usd must be finite and non-negative")
        if self.variant is not None and (
            not self.variant or self.variant != self.variant.strip() or len(self.variant) > 128
        ):
            raise ValueError("model price variant is invalid")


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    model_id: str
    display_name: str
    category: ModelCategory
    endpoint_kind: ModelEndpointKind
    description: str
    paid: bool
    availability: ModelAvailability
    unavailable_reason: str | None = None
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    context_length: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_streaming: bool = False
    supported_resolutions: tuple[str, ...] = ()
    supported_aspect_ratios: tuple[str, ...] = ()
    prices: tuple[ModelPrice, ...] = ()

    def __post_init__(self) -> None:
        allowed = MODEL_ALLOWLIST_BY_ID.get(self.model_id)
        if allowed is None:
            raise ValueError("model catalog entry is not allowlisted")
        if (
            self.display_name != allowed.display_name
            or self.category is not allowed.category
            or self.endpoint_kind is not allowed.endpoint_kind
            or self.paid is not allowed.paid
        ):
            raise ValueError("model catalog identity must match the allowlist")
        if not self.description or len(self.description) > 2_000:
            raise ValueError("model catalog description is invalid")
        if self.unavailable_reason is not None and (
            not self.unavailable_reason
            or self.unavailable_reason != self.unavailable_reason.strip()
            or len(self.unavailable_reason) > 128
        ):
            raise ValueError("model unavailable reason is invalid")
        if self.availability is ModelAvailability.AVAILABLE and self.unavailable_reason is not None:
            raise ValueError("available models cannot have an unavailable reason")
        for value in (*self.input_modalities, *self.output_modalities):
            if not value or value != value.strip() or len(value) > 64:
                raise ValueError("model modality is invalid")
        if self.context_length is not None and self.context_length <= 0:
            raise ValueError("model context length must be positive")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("model output limit must be positive")


@dataclass(frozen=True, slots=True)
class ProviderAccount:
    account_id: str
    provider_kind: str
    display_name: str
    credential_status: ProviderCredentialStatus

    def __post_init__(self) -> None:
        if self.account_id != "openrouter-default":
            raise ValueError("unsupported provider account")
        if self.provider_kind != "openrouter":
            raise ValueError("unsupported provider account kind")
        if not self.display_name or len(self.display_name) > 255:
            raise ValueError("provider account display name is invalid")


@dataclass(frozen=True, slots=True)
class ModelCatalogSnapshot:
    account: ProviderAccount
    entries: tuple[ModelCatalogEntry, ...]
    fetched_at: datetime
    expires_at: datetime
    revision: int
    last_error_code: str | None = None

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("model catalog revision cannot be negative")
        fetched_at = _aware(self.fetched_at)
        expires_at = _aware(self.expires_at)
        if expires_at <= fetched_at:
            raise ValueError("model catalog expiry must follow its fetch time")
        ids = tuple(entry.model_id for entry in self.entries)
        if len(ids) != len(set(ids)):
            raise ValueError("model catalog entries must be unique")
        if set(ids) != set(MODEL_ALLOWLIST_BY_ID):
            raise ValueError("model catalog must contain the complete allowlist")
        if self.last_error_code is not None and (
            not self.last_error_code
            or self.last_error_code != self.last_error_code.strip()
            or len(self.last_error_code) > 128
        ):
            raise ValueError("model catalog error code is invalid")
        object.__setattr__(self, "fetched_at", fetched_at)
        object.__setattr__(self, "expires_at", expires_at)

    def is_stale(self, now: datetime) -> bool:
        return _aware(now) >= self.expires_at

    def with_error(
        self,
        *,
        error_code: str,
        credential_status: ProviderCredentialStatus | None = None,
    ) -> ModelCatalogSnapshot:
        account = (
            replace(self.account, credential_status=credential_status)
            if credential_status is not None
            else self.account
        )
        return replace(self, account=account, last_error_code=error_code)


@dataclass(frozen=True, slots=True)
class ModelSelectionPreference:
    mode: ModelSelectionMode
    model_id: str | None
    allow_free_fallback: bool
    zero_data_retention: bool
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.mode is ModelSelectionMode.AUTO and self.model_id is not None:
            raise ValueError("auto model selection cannot include a model id")
        if self.mode is ModelSelectionMode.MANUAL and self.model_id not in MODEL_ALLOWLIST_BY_ID:
            raise ValueError("manual model selection must use an allowlisted model")
        if self.revision < 0:
            raise ValueError("model selection revision cannot be negative")
        object.__setattr__(self, "updated_at", _aware(self.updated_at))

    @classmethod
    def defaults(cls, *, now: datetime | None = None) -> ModelSelectionPreference:
        return cls(
            mode=ModelSelectionMode.AUTO,
            model_id=None,
            allow_free_fallback=False,
            zero_data_retention=False,
            revision=0,
            updated_at=now or datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class ModelSelectionSnapshot:
    mode: ModelSelectionMode
    model_id: str | None
    allow_free_fallback: bool
    zero_data_retention: bool
    revision: int
    captured_at: datetime

    def __post_init__(self) -> None:
        if self.mode is ModelSelectionMode.AUTO and self.model_id is not None:
            raise ValueError("auto model selection snapshot cannot include a model id")
        if self.mode is ModelSelectionMode.MANUAL and self.model_id not in MODEL_ALLOWLIST_BY_ID:
            raise ValueError("manual model selection snapshot must use an allowlisted model")
        if self.revision < 0:
            raise ValueError("model selection snapshot revision cannot be negative")
        object.__setattr__(self, "captured_at", _aware(self.captured_at))

    @classmethod
    def from_preference(
        cls,
        preference: ModelSelectionPreference,
        *,
        captured_at: datetime | None = None,
    ) -> ModelSelectionSnapshot:
        return cls(
            mode=preference.mode,
            model_id=preference.model_id,
            allow_free_fallback=preference.allow_free_fallback,
            zero_data_retention=preference.zero_data_retention,
            revision=preference.revision,
            captured_at=captured_at or datetime.now(UTC),
        )


def baseline_catalog(
    *,
    now: datetime,
    credential_status: ProviderCredentialStatus,
    revision: int = 0,
    error_code: str | None = "MODEL_CATALOG_NOT_REFRESHED",
) -> ModelCatalogSnapshot:
    fetched_at = _aware(now)
    return ModelCatalogSnapshot(
        account=ProviderAccount(
            account_id="openrouter-default",
            provider_kind="openrouter",
            display_name="OpenRouter",
            credential_status=credential_status,
        ),
        entries=tuple(
            ModelCatalogEntry(
                model_id=allowed.model_id,
                display_name=allowed.display_name,
                category=allowed.category,
                endpoint_kind=allowed.endpoint_kind,
                description=allowed.description,
                paid=allowed.paid,
                availability=ModelAvailability.UNKNOWN,
                unavailable_reason="CATALOG_NOT_REFRESHED",
            )
            for allowed in MODEL_ALLOWLIST
        ),
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(hours=6),
        revision=revision,
        last_error_code=error_code,
    )


def merge_catalog_entries(
    entries: Mapping[str, ModelCatalogEntry],
) -> tuple[ModelCatalogEntry, ...]:
    return tuple(
        entries.get(allowed.model_id)
        or ModelCatalogEntry(
            model_id=allowed.model_id,
            display_name=allowed.display_name,
            category=allowed.category,
            endpoint_kind=allowed.endpoint_kind,
            description=allowed.description,
            paid=allowed.paid,
            availability=ModelAvailability.UNAVAILABLE,
            unavailable_reason="MODEL_NOT_LISTED",
        )
        for allowed in MODEL_ALLOWLIST
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("model catalog timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "MODEL_ALLOWLIST",
    "MODEL_ALLOWLIST_BY_ID",
    "AllowedModel",
    "ModelAvailability",
    "ModelCatalogEntry",
    "ModelCatalogSnapshot",
    "ModelCategory",
    "ModelEndpointKind",
    "ModelPrice",
    "ModelSelectionMode",
    "ModelSelectionPreference",
    "ModelSelectionSnapshot",
    "ProviderAccount",
    "ProviderCredentialStatus",
    "baseline_catalog",
    "merge_catalog_entries",
]
