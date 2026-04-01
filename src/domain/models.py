"""Domain models for geographic layers and job results."""


class PolygonTooLargeError(ValueError):
    """Raised when the serialized polygon exceeds the database column limit."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class LayerType(Enum):
    """Enumeration of supported geographic layer types."""

    COUNTRY = "country"
    DEPARTMENTS = "departments"


@dataclass
class LayerRefreshResult:
    """Result of a layer refresh job run."""

    status: str
    files: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: Optional[str] = None
