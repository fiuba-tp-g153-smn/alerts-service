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


class AreaTooLargeError(ValueError):
    """Raised when the affected-area HTML exceeds the database column limit.

    Unlike the polygon size (known up front), the affected area depends on the
    intersection result, so this can only be detected during generation. Carries
    the column limit, the actual length, and the number of affected departments
    so the user can be told to reduce the polygon.
    """

    def __init__(
        self, message: str, max_chars: int, actual_chars: int, affected_count: int
    ):
        super().__init__(message)
        self.max_chars = max_chars
        self.actual_chars = actual_chars
        self.affected_count = affected_count


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
