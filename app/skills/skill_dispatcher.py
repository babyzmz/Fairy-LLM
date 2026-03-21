"""Integration guide for lazy skill router into main application.

This module shows how to integrate the new lazy skill router
into your existing application.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.skills.lazy_router import LazySkillRouter, RoutingDecision
from app.skills.legacy_isolation import get_legacy_registry, set_legacy_enabled

logger = logging.getLogger(__name__)


class SkillDispatcher:
    """Main dispatcher that routes queries to appropriate skills."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize dispatcher with configuration.

        Args:
            config_path: Path to lazy_skills_config.json
        """
        self.config = self._load_config(config_path)
        self.router = LazySkillRouter(self.config)
        self._setup_legacy_skills()

    def _load_config(self, config_path: str | Path | None) -> dict[str, Any]:
        """Load configuration from file or use defaults."""
        if config_path is None:
            config_path = Path("config/lazy_skills_config.json")

        if isinstance(config_path, str):
            config_path = Path(config_path)

        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                logger.info("Loaded config from %s", config_path)
                return config
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", config_path, e)

        # Return defaults
        return {
            "use_lazy_skills": True,
            "use_legacy_fallback": False,
            "enable_debug_logging": False,
            "graceful_fallback": True,
            "prefer_realtime": True,
            "realtime_timeout_sec": 8,
            "web_research_timeout_sec": 15,
        }

    def _setup_legacy_skills(self) -> None:
        """Setup legacy skill registry based on config."""
        use_legacy = self.config.get("use_legacy_fallback", False)
        if use_legacy:
            logger.warning(
                "Legacy fallback is enabled. "
                "Consider disabling for cleaner architecture."
            )
            set_legacy_enabled(True)
        else:
            set_legacy_enabled(False)
            logger.info("Legacy skills are isolated and disabled by default")

    def dispatch(
        self,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """Route query to appropriate skill bundle.

        Args:
            query: User query
            context: Optional context (user preferences, history, etc.)

        Returns:
            RoutingDecision with skill bundle and metadata
        """
        decision = self.router.route(query, context)

        if self.config.get("enable_debug_logging"):
            logger.info(
                "Routing decision: bundle=%s intent=%s confidence=%.2f reason=%s",
                decision.skill_bundle,
                decision.intent_type,
                decision.confidence,
                decision.reason,
            )

        return decision

    def execute(
        self,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute query using appropriate skill.

        Args:
            query: User query
            context: Optional context

        Returns:
            Execution result
        """
        decision = self.dispatch(query, context)

        # Route to appropriate executor
        if decision.skill_bundle == "realtime_lookup":
            return self._execute_realtime(query, decision, context)
        elif decision.skill_bundle == "web_research":
            return self._execute_web_research(query, decision, context)
        elif decision.skill_bundle == "news_intelligence":
            return self._execute_news(query, decision, context)
        elif decision.skill_bundle == "document_editing":
            return self._execute_document(query, decision, context)
        elif decision.skill_bundle == "terminal_agent":
            return self._execute_terminal(query, decision, context)
        else:
            return self._execute_default(query, decision, context)

    def _execute_realtime(
        self,
        query: str,
        decision: RoutingDecision,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute realtime lookup."""
        # TODO: Implement realtime executor
        logger.info("Executing realtime lookup: %s", decision.intent_type)
        return {
            "status": "pending",
            "message": "Realtime executor not yet implemented",
            "intent": decision.intent_type,
        }

    def _execute_web_research(
        self,
        query: str,
        decision: RoutingDecision,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute web research."""
        # TODO: Integrate with existing web_research_skill
        logger.info("Executing web research")
        return {
            "status": "pending",
            "message": "Web research executor",
        }

    def _execute_news(
        self,
        query: str,
        decision: RoutingDecision,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute news intelligence."""
        # TODO: Integrate with existing news_intelligence_skill
        logger.info("Executing news intelligence")
        return {
            "status": "pending",
            "message": "News intelligence executor",
        }

    def _execute_document(
        self,
        query: str,
        decision: RoutingDecision,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute document editing."""
        # TODO: Integrate with existing document_editor_skill
        logger.info("Executing document editing")
        return {
            "status": "pending",
            "message": "Document editing executor",
        }

    def _execute_terminal(
        self,
        query: str,
        decision: RoutingDecision,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute terminal agent."""
        # TODO: Integrate with existing agent_shell_skill
        logger.info("Executing terminal agent")
        return {
            "status": "pending",
            "message": "Terminal agent executor",
        }

    def _execute_default(
        self,
        query: str,
        decision: RoutingDecision,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute default handler."""
        logger.warning("No executor for skill bundle: %s", decision.skill_bundle)
        return {
            "status": "error",
            "message": f"No executor for {decision.skill_bundle}",
        }


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Create dispatcher
    dispatcher = SkillDispatcher()

    # Test queries
    test_queries = [
        "What's the weather in Melbourne?",
        "USD to CNY exchange rate",
        "AAPL stock price",
        "Bitcoin price",
        "Lakers vs Celtics score",
        "Petrol price in Sydney",
        "Latest tech news",
        "How to learn Python?",
    ]

    print("\n" + "=" * 60)
    print("SKILL ROUTING DEMONSTRATION")
    print("=" * 60 + "\n")

    for query in test_queries:
        decision = dispatcher.dispatch(query)
        print(f"Query: {query}")
        print(f"  → Skill: {decision.skill_bundle}")
        print(f"  → Intent: {decision.intent_type}")
        print(f"  → Confidence: {decision.confidence:.0%}")
        print(f"  → Reason: {decision.reason}")
        print()
