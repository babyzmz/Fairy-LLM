from __future__ import annotations

from app.response.card_schema_registry import CardSchemaRegistry
from app.response.models import CardPayload


class SchemaNormalizer:
    def __init__(self, registry: CardSchemaRegistry | None = None) -> None:
        self._registry = registry or CardSchemaRegistry()

    def normalize(self, card_type: str, raw_data: dict, *, layout: str = "single", metadata: dict | None = None) -> CardPayload | None:
        return self._registry.normalize_card_payload(
            card_type,
            raw_data,
            layout=layout,
            metadata=metadata,
        )
