from __future__ import annotations

from dataclasses import dataclass, field

from app.response.models import CardPayload
from app.schemas.generic_info_schema import GenericInfoSchema
from app.schemas.location_schema import LocationSchema
from app.schemas.news_schema import NewsListSchema
from app.schemas.weather_schema import WeatherSchema


@dataclass(slots=True)
class SchemaValidationResult:
    valid: bool
    repaired_card: CardPayload
    issues: list[str] = field(default_factory=list)


class SchemaValidator:
    def validate_card(self, card: CardPayload) -> SchemaValidationResult:
        metadata = dict(card.metadata)
        if card.type == "weather":
            schema = WeatherSchema.repair(card.data)
            return SchemaValidationResult(
                valid=schema.is_valid(),
                repaired_card=CardPayload(type="weather", version=card.version, data=schema.to_dict(), layout=card.layout, metadata=metadata),
                issues=[] if schema.is_valid() else ["weather_schema_incomplete"],
            )
        if card.type == "location":
            schema = LocationSchema.repair(card.data)
            return SchemaValidationResult(
                valid=schema.is_valid(),
                repaired_card=CardPayload(type="location", version=card.version, data=schema.to_dict(), layout=card.layout, metadata=metadata),
                issues=[] if schema.is_valid() else ["location_schema_incomplete"],
            )
        if card.type == "news_list":
            schema = NewsListSchema.repair(card.data)
            return SchemaValidationResult(
                valid=schema.is_valid(),
                repaired_card=CardPayload(type="news_list", version=card.version, data=schema.to_dict(), layout=card.layout, metadata=metadata),
                issues=[] if schema.is_valid() else ["news_schema_incomplete"],
            )
        if card.type not in {"generic_info"}:
            metadata.setdefault("fallback_reason", "unknown_schema_type")
        schema = GenericInfoSchema.repair(card.data)
        return SchemaValidationResult(
            valid=schema.is_valid(),
            repaired_card=CardPayload(type="generic_info", version=card.version, data=schema.to_dict(), layout=card.layout, metadata=metadata),
            issues=[] if schema.is_valid() else ["generic_info_schema_incomplete"],
        )
