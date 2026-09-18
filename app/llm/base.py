"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import List


class LLMProvider(ABC):
    """Abstract interface for LLM-based operator-note interpretation."""

    @abstractmethod
    async def interpret_notes(
        self,
        operator_notes: List[str],
        battery_capacity_kwh: float,
    ) -> List[dict]:
        """
        Interpret operator notes into structured directive dictionaries.

        Args:
            operator_notes: List of 1-3 natural-language operator notes.
            battery_capacity_kwh: Battery capacity for percentage conversion.

        Returns:
            List of directive interpretation dicts, one per note.

        Raises:
            LLMError: If the LLM call fails.
        """
        ...


class LLMError(Exception):
    """Raised when an LLM call fails."""
    pass
