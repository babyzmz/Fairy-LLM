"""Page session — manages open-page state within a single request.

Not a conversation state store. Scoped to one agent invocation.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PageSession:
    pages_opened: list[str] = field(default_factory=list)
    max_pages: int = 5

    def record(self, url: str) -> bool:
        """Record a page open. Returns False if limit exceeded."""
        if len(self.pages_opened) >= self.max_pages:
            return False
        self.pages_opened.append(url)
        return True

    def exhausted(self) -> bool:
        return len(self.pages_opened) >= self.max_pages
