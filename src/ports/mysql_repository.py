"""Abstract interface for MySQL alert operations."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class IMySQLRepository(ABC):
    """Port for MySQL database operations."""

    @abstractmethod
    def get_departments(self) -> List[dict]:
        """Return all departments with coordinates."""

    @abstractmethod
    def insert_alert(
        self,
        phenomenon: str,
        area: str,
        polygon: str,
        gif_general: str,
        gif_zoom: str,
    ) -> int:
        """Insert alert record and return ID."""

    @abstractmethod
    def get_pending_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        """Return pending alerts, optionally only those with IdAviso_temporal
        greater than since_id, ordered by id."""

    @abstractmethod
    def get_pending_alerts_etag(self) -> Tuple[int, Optional[int]]:
        """Return (count, max_id) of pending alerts for ETag computation.

        `count` detects removals (Procesado 'N'->'Y'); `max_id` detects new
        insertions (ids are monotonic). Together they change whenever the
        pending set gains or loses a member. `max_id` is None when empty."""

    @abstractmethod
    def get_polygon_max_length(self) -> int:
        """Return the VARCHAR character limit of the taviso_temporal.Poligono column.

        Used to validate the serialized polygon before insertion. Raises if the
        limit cannot be determined (column missing or no length defined)."""

    @abstractmethod
    def get_area_max_length(self) -> int:
        """Return the VARCHAR character limit of the taviso_temporal.Area column.

        Used to validate the affected-area HTML before insertion. Raises if the
        limit cannot be determined (column missing or no length defined)."""

    @abstractmethod
    def get_phenomenon_text(self, code: int) -> Optional[str]:
        """Get phenomenon description by code."""

    @abstractmethod
    def get_all_phenomena(self) -> Dict[int, Optional[str]]:
        """Get all phenomenon codes and descriptions."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
