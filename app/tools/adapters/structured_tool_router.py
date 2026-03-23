from __future__ import annotations


class StructuredToolRouter:
    def determine_tool_chain(self, intent: str) -> list[str]:
        if intent == "weather_lookup":
            return ["weather_lookup", "render_cards", "speak_summary"]
        if intent in {"location_lookup", "display_information"}:
            return ["location_lookup", "render_cards", "speak_summary"]
        if intent == "news_lookup":
            return ["search_web", "summarize", "render_cards", "speak_summary"]
        if intent == "system_action":
            return ["capture_screen", "summarize"]
        return ["direct_answer"]
