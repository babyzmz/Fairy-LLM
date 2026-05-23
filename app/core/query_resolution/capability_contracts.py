from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CapabilitySlotContract:
    capability: str
    required_slots: list[str]
    optional_slots: list[str] = field(default_factory=list)
    default_slot_sources: dict[str, list[str]] = field(default_factory=dict)
    clarification_messages: dict[str, str] = field(default_factory=dict)
    slot_normalizers: dict[str, str] = field(default_factory=dict)
    slot_validators: dict[str, str] = field(default_factory=dict)
    carryover_safe_slots: dict[str, bool] = field(default_factory=dict)
    carryover_sources: dict[str, list[str]] = field(default_factory=dict)
    allow_partial: bool = False
    supports_followup: bool = True


class CapabilityContractRegistry:
    def __init__(self) -> None:
        self._contracts = {
            "weather_lookup": CapabilitySlotContract(
                capability="weather_lookup",
                required_slots=["location"],
                optional_slots=["date"],
                default_slot_sources={
                    "location": [
                        "followup_context",
                        "session_last_weather_location",
                        "session_last_location",
                        "session_last_city",
                        "previous_weather_card",
                        "previous_location_card",
                        "detected_current_city",
                        "configured_default_city",
                    ],
                    "date": ["explicit_date", "default_today"],
                },
                clarification_messages={"location": "你想看哪个城市的天气？"},
                slot_normalizers={"location": "location", "date": "date"},
                slot_validators={"location": "location", "date": "date"},
                carryover_safe_slots={"location": True, "date": True},
                carryover_sources={
                    "location": [
                        "followup_context",
                        "session_last_weather_location",
                        "session_last_location",
                        "session_last_city",
                        "previous_weather_card",
                        "previous_location_card",
                    ],
                    "date": [],
                },
            ),
            "time_lookup": CapabilitySlotContract(
                capability="time_lookup",
                required_slots=["location"],
                optional_slots=["date", "timezone_hint"],
                default_slot_sources={
                    "location": [
                        "session_last_weather_location",
                        "session_last_location",
                        "previous_location_card",
                        "previous_weather_card",
                        "followup_context",
                        "session_last_city",
                        "detected_current_city",
                        "configured_default_city",
                    ],
                    "date": ["explicit_date", "default_today"],
                },
                clarification_messages={"location": "你想看哪个城市或地区的时间？"},
                slot_normalizers={"location": "location", "date": "date"},
                slot_validators={"location": "location", "date": "date"},
                carryover_safe_slots={"location": True, "date": True, "timezone_hint": False},
                carryover_sources={
                    "location": [
                        "session_last_weather_location",
                        "session_last_location",
                        "previous_location_card",
                        "previous_weather_card",
                        "followup_context",
                        "session_last_city",
                    ],
                    "date": [],
                },
            ),
            "location_lookup": CapabilitySlotContract(
                capability="location_lookup",
                required_slots=["location"],
                clarification_messages={"location": "你想查看哪个地点？"},
                slot_normalizers={"location": "location"},
                slot_validators={"location": "location"},
                carryover_safe_slots={"location": True},
                carryover_sources={"location": ["followup_context"]},
            ),
            "display_information": CapabilitySlotContract(
                capability="display_information",
                required_slots=["location"],
                optional_slots=["view_mode"],
                default_slot_sources={"location": ["followup_context", "session_last_location", "previous_location_card"]},
                clarification_messages={"location": "你想显示哪个地点的地图？"},
                slot_normalizers={"location": "location"},
                slot_validators={"location": "location"},
                carryover_safe_slots={"location": True},
                carryover_sources={"location": ["followup_context", "session_last_location", "previous_location_card"]},
            ),
            "news_lookup": CapabilitySlotContract(
                capability="news_lookup",
                required_slots=["topic"],
                optional_slots=["date", "source"],
                default_slot_sources={
                    "topic": ["explicit_topic", "topic_keyword"],
                    "date": ["explicit_date", "default_today"],
                },
                clarification_messages={"topic": "你想看哪一类新闻？"},
                slot_normalizers={"topic": "topic", "date": "date"},
                slot_validators={"topic": "topic", "date": "date"},
                carryover_safe_slots={"topic": True, "date": True, "source": False},
                carryover_sources={"topic": ["followup_context"], "date": []},
            ),
            "generic_search": CapabilitySlotContract(
                capability="generic_search",
                required_slots=["query"],
                optional_slots=["topic"],
                default_slot_sources={
                    "query": ["normalized_query"],
                    "topic": ["topic_keyword", "followup_context"],
                },
                clarification_messages={"query": "你想查什么内容？"},
                slot_normalizers={"query": "query", "topic": "topic"},
                slot_validators={"query": "query", "topic": "topic"},
                carryover_safe_slots={"query": False, "topic": True},
                carryover_sources={"topic": ["followup_context"]},
            ),
            "explanation": CapabilitySlotContract(
                capability="explanation",
                required_slots=["topic"],
                optional_slots=["context_entity"],
                default_slot_sources={
                    "topic": ["explicit_topic", "followup_context", "previous_card_topic", "previous_user_query_focus"],
                },
                clarification_messages={"topic": "你是想让我解释什么？"},
                slot_normalizers={"topic": "query", "context_entity": "topic"},
                slot_validators={"topic": "topic", "context_entity": "topic"},
                carryover_safe_slots={"topic": True, "context_entity": True},
                carryover_sources={
                    "topic": ["followup_context", "previous_card_topic", "previous_user_query_focus"],
                    "context_entity": ["followup_context"],
                },
            ),
            "system_action": CapabilitySlotContract(
                capability="system_action",
                required_slots=["action_name"],
                optional_slots=["target"],
                default_slot_sources={"target": ["current_ui_context", "followup_context"]},
                clarification_messages={"action_name": "你想让我执行什么操作？"},
                slot_normalizers={"target": "query"},
                slot_validators={"action_name": "query", "target": "query"},
                carryover_safe_slots={"action_name": False, "target": True},
                carryover_sources={"target": ["current_ui_context", "followup_context"]},
            ),
        }

    def get(self, capability: str) -> CapabilitySlotContract | None:
        return self._contracts.get(str(capability or "").strip().lower())

    def all(self) -> dict[str, CapabilitySlotContract]:
        return dict(self._contracts)
