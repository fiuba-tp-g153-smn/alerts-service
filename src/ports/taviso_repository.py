"""Abstract interface for read-only access to the external taviso database."""

from abc import ABC, abstractmethod
from typing import List, Optional


class ITavisoReadRepository(ABC):
    """Read-only port for the external MySQL `taviso` table."""

    @abstractmethod
    def get_active_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        """Return active alerts (started and not expired), optionally only those
        with IdAlerta greater than since_id, ordered by IdAlerta."""

    @abstractmethod
    def get_max_active_alert_id(self) -> Optional[int]:
        """Return the highest IdAlerta among active alerts, or None if none."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
