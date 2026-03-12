"""Port definition for job run history data access."""

from typing import List, Optional, Protocol


class IHistoryRepository(Protocol):
    """Protocol for history repository implementations."""

    def record_run(
        self,
        status: str,
        files: Optional[List[str]],
        duration_sec: Optional[float],
        error: Optional[str],
    ) -> None:
        """Record the outcome of a layer refresh job run."""

    def get_recent(self, limit: int) -> list[dict]:
        """Return the most recent job run records up to the given limit."""
