"""Tests for GeoLayerProcessor — subprocess cancellation behaviour."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.geo_layer_processor import _run_worker


async def test_run_worker_terminates_subprocess_on_cancellation():
    """When the task is cancelled, the child process must be terminated."""
    mock_proc = AsyncMock()
    mock_proc.terminate = MagicMock()
    mock_proc.returncode = 0

    async def slow_communicate(_input):
        await asyncio.sleep(60)  # simulate long-running subprocess
        return b"", b""

    mock_proc.communicate.side_effect = slow_communicate

    with patch(
        "adapters.geo_layer_processor.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        task = asyncio.create_task(_run_worker([{"op": "simplify"}]))
        await asyncio.sleep(0)  # let the task start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once()


async def test_run_worker_raises_on_nonzero_returncode():
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (b"", b"error output")

    with patch(
        "adapters.geo_layer_processor.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        with pytest.raises(subprocess.CalledProcessError):
            await _run_worker([{"op": "simplify"}])
