"""Port definition for object storage backends."""

from typing import Protocol


class IObjectStorage(Protocol):
    """Protocol for async object storage implementations."""

    async def upload(self, local_path: str, key: str) -> None:
        """Upload a local file to the storage backend under the given key."""

    async def download(self, key: str, local_path: str) -> bool:
        """Download an object by key to a local path; return False if not found."""

    async def delete(self, key: str) -> None:
        """Delete the object identified by key from storage."""

    async def list_keys(self, prefix: str) -> list[str]:
        """List all object keys matching the given prefix."""
