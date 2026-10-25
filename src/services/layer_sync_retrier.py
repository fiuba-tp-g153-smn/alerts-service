"""Background retrier that heals geo layers after a degraded startup.

Startup never fails on an S3 or IGN outage: it reconciles local-only and leaves
missing layers missing. This single long-lived asyncio task (same lifecycle as
MetricsSampler) re-runs the sync with capped exponential backoff until S3 is
consistent, every layer exists and the alert caches are rebuilt, then exits.
"""

import asyncio
from logging import Logger
from typing import Awaitable, Callable, Optional, Protocol


class LayerSync(Protocol):
    """The slice of GeoLayerSyncService the retrier drives."""

    @property
    def s3_degraded(self) -> bool:
        """True when the last sync could not reach S3."""

    async def ensure_all(self) -> list:
        """Reconcile local ↔ S3; return the layers needing re-generation."""

    async def regenerate(self) -> bool:
        """Re-generate the flagged layers; True if all succeeded."""


class LayerSyncRetrier:
    """Retries the geo layer sync until healthy, backing off between attempts."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        sync: LayerSync,
        refresh_caches: Callable[[], Awaitable[bool]],
        *,
        caches_ready: bool,
        initial_delay: float,
        max_delay: float,
        logger: Logger,
    ):
        """Initialize with the sync service, cache-refresh hook and backoff bounds.

        ``refresh_caches`` rebuilds the alert caches from the local layers and
        returns whether every alert layer is ready; ``caches_ready`` says whether
        the startup build already covered them.
        """
        self._sync = sync
        self._refresh_caches = refresh_caches
        self._caches_ready = caches_ready
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._logger = logger
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Spawn the retry loop (idempotent)."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="layer-sync-retrier")
        self._logger.warning(
            "Geo layers degraded — retrying sync in background (first in %.0fs)",
            self._initial_delay,
        )

    async def stop(self) -> None:
        """Cancel the retry loop (if still running) and wait for it to finish."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        delay = self._initial_delay
        while True:
            await asyncio.sleep(delay)
            if await self._attempt_safely():
                self._logger.info("Geo layers healed — background sync retrier done.")
                return
            delay = min(delay * 2, self._max_delay)
            self._logger.warning(
                "Geo layers still degraded — next sync attempt in %.0fs", delay
            )

    async def _attempt_safely(self) -> bool:
        """Run one attempt; an unexpected error counts as a failed attempt."""
        try:
            return await self._attempt()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._logger.warning("Background layer sync attempt failed: %s", exc)
            return False

    async def _attempt(self) -> bool:
        """Sync once; True when S3 is consistent, layers exist and caches are built."""
        needs_regen = await self._sync.ensure_all()
        if needs_regen and not await self._sync.regenerate():
            return False
        if needs_regen or not self._caches_ready:
            self._caches_ready = await self._refresh_caches()
        return self._caches_ready and not self._sync.s3_degraded
