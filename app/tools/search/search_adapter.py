"""Abstract search adapter — provider-agnostic search interface."""
from __future__ import annotations
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class SearchAdapter(Protocol):
    """Protocol for any search backend."""
    def search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]: ...


class FunctionSearchAdapter:
    """Wraps a plain callable (e.g. search_web tool) as a SearchAdapter."""

    def __init__(self, fn: Callable) -> None:
        self._fn = fn

    def search(self, query: str, max_results: int = 8) -> list[dict[str, Any]]:
        try:
            result = self._fn(query, max_results=max_results)
            if isinstance(result, list):
                return result
            return []
        except TypeError:
            # Some tools don't accept max_results kwarg
            try:
                result = self._fn(query)
                return result if isinstance(result, list) else []
            except Exception:
                return []
        except Exception:
            return []
