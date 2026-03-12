"""Port definition for job run history data access."""

from abc import ABC, abstractmethod
from typing import List, Optional


class IHistoryRepository(ABC):
    """Abstract base class for history repository implementations."""

    @abstractmethod
    def record_run(
        self,
        status: str,
        files: Optional[List[str]],
        duration_sec: Optional[float],
        error: Optional[str],
    ) -> None:
        """Record the outcome of a layer refresh job run."""

    @abstractmethod
    def get_recent(self, limit: int) -> list[dict]:
        """Return the most recent job run records up to the given limit."""
