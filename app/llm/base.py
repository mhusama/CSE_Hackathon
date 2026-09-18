"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import List, Optional


class LLMProvider(ABC):
    """Abstract interface for LLM-based operator-note interpretation."""

    @abstractmethod
    async def interpret_notes(
        self,
        operator_notes: List[str],
        battery_capacity_kwh: float,
        feedback: Optional[str] = None,
    ) -> List[dict]:
        """
        Interpret operator notes into raw directive dictionaries (one per note).

        `feedback`, when given, lists problems found in the previous answer so the
        model can correct itself.

        Raises:
            LLMError: If the call fails.
        """
        ...


class LLMError(Exception):
    """Raised when an LLM call fails."""
    pass


class RateLimitError(LLMError):
    """HTTP 429 from the provider. retry_after is in seconds when the API says so."""

    def __init__(self, message: str, retry_after: float = None):
        super().__init__(message)
        self.retry_after = retry_after
