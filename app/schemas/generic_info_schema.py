from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GenericFieldSchema:
    label: str
    value: str

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "GenericFieldSchema | None":
        label = str(payload.get("label") or payload.get("name") or "").strip()
        value = str(payload.get("value") or "").strip()
        if not label or not value:
            return None
        return cls(label=label, value=value)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value}


@dataclass(slots=True)
class GenericInfoSchema:
    title: str
    summary: str
    fields: list[GenericFieldSchema]

    @classmethod
    def repair(cls, payload: dict[str, Any]) -> "GenericInfoSchema":
        fields: list[GenericFieldSchema] = []
        for item in list(payload.get("fields", []) or []):
            if not isinstance(item, dict):
                continue
            repaired = GenericFieldSchema.repair(item)
            if repaired is not None:
                fields.append(repaired)
        title = str(payload.get("title") or "Structured info").strip() or "Structured info"
        summary = str(payload.get("summary") or payload.get("text") or "").strip()
        return cls(title=title, summary=summary, fields=fields)

    def is_valid(self) -> bool:
        return bool(self.summary or self.fields)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "summary": self.summary, "fields": [field.to_dict() for field in self.fields]}
