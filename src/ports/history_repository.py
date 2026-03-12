from typing import List, Optional, Protocol


class IHistoryRepository(Protocol):
    def record_run(
        self,
        status: str,
        files: Optional[List[str]],
        duration_sec: Optional[float],
        error: Optional[str],
    ) -> None: ...

    def get_recent(self, limit: int) -> list[dict]: ...
