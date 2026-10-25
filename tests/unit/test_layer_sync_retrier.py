"""Tests for LayerSyncRetrier — background healing after a degraded startup."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from services.layer_sync_retrier import LayerSyncRetrier


class _FakeSync:
    """Scripted LayerSync: each ensure_all() pops the next (degraded, needs) step."""

    def __init__(self, steps, regenerate_ok=True):
        self._steps = list(steps)
        self._regenerate_ok = regenerate_ok
        self.s3_degraded = True
        self.ensure_calls = 0
        self.regenerate_calls = 0

    async def ensure_all(self):
        self.ensure_calls += 1
        step = self._steps.pop(0) if self._steps else (False, [])
        if isinstance(step, Exception):
            raise step
        self.s3_degraded, needs = step
        return needs

    async def regenerate(self):
        self.regenerate_calls += 1
        return self._regenerate_ok


def _retrier(sync, refresh=None, caches_ready=True, max_delay=0.0):
    return LayerSyncRetrier(
        sync,
        refresh or AsyncMock(return_value=True),
        caches_ready=caches_ready,
        initial_delay=0.0,
        max_delay=max_delay,
        logger=MagicMock(),
    )


async def _run(retrier, timeout=1.0):
    retrier.start()
    await asyncio.wait_for(asyncio.shield(retrier._task), timeout)


async def test_retries_while_s3_degraded_then_exits_once_healthy():
    sync = _FakeSync([(True, []), (True, []), (False, [])])
    refresh = AsyncMock(return_value=True)

    await _run(_retrier(sync, refresh))

    assert sync.ensure_calls == 3
    refresh.assert_not_awaited()  # caches were already built at startup


async def test_regenerates_missing_layers_and_refreshes_caches_once():
    sync = _FakeSync([(False, ["departamentos"])])
    refresh = AsyncMock(return_value=True)

    await _run(_retrier(sync, refresh, caches_ready=False))

    assert sync.regenerate_calls == 1
    refresh.assert_awaited_once()


async def test_keeps_retrying_while_regeneration_fails():
    sync = _FakeSync([(False, ["pais"])] * 3, regenerate_ok=False)
    retrier = _retrier(sync)
    retrier.start()

    while sync.ensure_calls < 3:
        await asyncio.sleep(0)
    await retrier.stop()

    assert sync.regenerate_calls >= 3


async def test_keeps_retrying_until_alert_caches_are_ready():
    sync = _FakeSync([(False, []), (False, [])])
    refresh = AsyncMock(side_effect=[False, True])

    await _run(_retrier(sync, refresh, caches_ready=False))

    assert refresh.await_count == 2


async def test_unexpected_error_does_not_kill_the_loop():
    sync = _FakeSync([RuntimeError("boom"), (False, [])])

    await _run(_retrier(sync))

    assert sync.ensure_calls == 2


async def test_backoff_doubles_up_to_the_cap(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("services.layer_sync_retrier.asyncio.sleep", fake_sleep)
    sync = _FakeSync([(True, [])] * 5 + [(False, [])])
    retrier = LayerSyncRetrier(
        sync,
        AsyncMock(return_value=True),
        caches_ready=True,
        initial_delay=30.0,
        max_delay=200.0,
        logger=MagicMock(),
    )

    await retrier._loop()

    assert delays == [30.0, 60.0, 120.0, 200.0, 200.0, 200.0]


async def test_stop_cancels_a_pending_retry():
    retrier = LayerSyncRetrier(
        _FakeSync([]),
        AsyncMock(return_value=True),
        caches_ready=True,
        initial_delay=3600.0,
        max_delay=3600.0,
        logger=MagicMock(),
    )
    retrier.start()

    await retrier.stop()  # returns promptly despite the hour-long sleep

    assert retrier._task is None
