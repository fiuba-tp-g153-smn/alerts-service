from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class LayerType(Enum):
    COUNTRY = "country"
    DEPARTMENTS = "departments"


@dataclass
class LayerRefreshResult:
    status: str
    files: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: Optional[str] = None
