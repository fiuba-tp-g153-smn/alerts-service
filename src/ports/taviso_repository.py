"""Abstract interface for read-only access to the external taviso database."""

from abc import ABC, abstractmethod
from typing import List


class ITavisoReadRepository(ABC):
    """Read-only port for the external MySQL `taviso` table."""

    @abstractmethod
    def fetch_alerts(self, limit: int = 100) -> List[dict]:
        """Return the most recent alerts from the `taviso` table."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
