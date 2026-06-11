"""Domain models for geographic layers and job results."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class PolygonTooLargeError(ValueError):
    """Raised when the serialized polygon exceeds the database column limit.

    Carries `max_vertex_count`: the maximum number of vertices the input polygon
    may have to fit the column, derived from the column's character limit.
    """

    def __init__(self, message: str, max_vertex_count: int):
        super().__init__(message)
        self.max_vertex_count = max_vertex_count


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
