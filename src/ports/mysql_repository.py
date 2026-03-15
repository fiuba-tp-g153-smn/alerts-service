"""Abstract interface for MySQL alert operations."""

from abc import ABC, abstractmethod
from typing import List, Optional


class IMySQLRepository(ABC):
    """Port for MySQL database operations."""

    @abstractmethod
    def get_partidos(self) -> List[dict]:
        """Return all partidos with coordinates."""

    @abstractmethod
    def insert_taviso(self, fenomeno: str, area: str, poligono: str) -> int:
        """Insert alert record and return ID."""

    @abstractmethod
    def get_fenomeno_text(self, code: int) -> Optional[str]:
        """Get phenomenon description by code."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
