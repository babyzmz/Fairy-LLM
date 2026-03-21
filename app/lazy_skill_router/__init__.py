"""
Fairy Lazy Skill Router Package
================================

New Anthropic-style skill routing system with lazy loading.
"""

from .lazy_loader import SkillBundle, SkillLazyLoader, SkillMetadata
from .router import LazyRouteContext, LazyRouteDecision, LazySkillRouter

__all__ = [
    "LazyRouteContext",
    "LazyRouteDecision",
    "LazySkillRouter",
    "SkillBundle",
    "SkillLazyLoader",
    "SkillMetadata",
]
