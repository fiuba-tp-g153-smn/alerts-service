"""Domain models for asynchronous alert generation jobs."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class JobStatus(Enum):
    """Lifecycle states of a background alert generation job."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AlertJob:
    """Work item enqueued for background alert generation."""

    job_id: str
    geometry: dict
    phenomenon_code: int
    phenomenon_text: str


@dataclass(frozen=True, slots=True)
class AlertJobRecord:
    """Status snapshot of a job, kept in the in-memory registry.

    Immutable: each lifecycle transition replaces the registry entry wholesale.
    """

    status: JobStatus
    alert_id: Optional[int] = None
    error_code: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PreparedAlert:
    """Result of validating an alert request before it is enqueued."""

    phenomenon_text: str
    polygon_str: str
