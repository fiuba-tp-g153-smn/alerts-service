"""Abstract interface for MySQL alert operations."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


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
    def get_phenomenon_text(self, code: int) -> Optional[str]:
        """Get phenomenon description by code."""

    @abstractmethod
    def get_all_phenomena(self) -> Dict[int, Optional[str]]:
        """Get all phenomenon codes and descriptions."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
