"""Tests for GeoLayerProcessor — subprocess cancellation and streaming download."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.geo_layer_processor import GeoLayerProcessor, _run_worker


class _FakeResponse:
    """Minimal aiohttp response: streams chunks, no read() (would buffer whole body)."""

    def __init__(self, chunks, raise_mid_stream=False):
        self._chunks = chunks
        self._raise_mid_stream = raise_mid_stream
        self.content = self

    def raise_for_status(self):
        pass

    async def iter_chunked(self, _size):
        for chunk in self._chunks:
            yield chunk
        if self._raise_mid_stream:
            raise RuntimeError("connection reset")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def get(self, _url, timeout=None):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


async def test_download_streams_chunks_without_buffering(tmp_path):
    resp = _FakeResponse([b"hello ", b"world", b"!"])
    out = tmp_path / "layer.geojson"
    with patch(
        "adapters.geo_layer_processor.aiohttp.ClientSession",
        return_value=_FakeSession(resp),
    ):
        await GeoLayerProcessor(MagicMock()).download("http://x/layer", str(out))

    assert out.read_bytes() == b"hello world!"
    assert not (tmp_path / "layer.geojson.tmp").exists()  # atomic rename cleaned tmp


async def test_download_removes_partial_on_failure(tmp_path):
    resp = _FakeResponse([b"partial"], raise_mid_stream=True)
    out = tmp_path / "layer.geojson"
    with patch(
        "adapters.geo_layer_processor.aiohttp.ClientSession",
        return_value=_FakeSession(resp),
    ):
        with pytest.raises(RuntimeError, match="connection reset"):
            await GeoLayerProcessor(MagicMock()).download("http://x/layer", str(out))

    assert not out.exists()
    assert not (tmp_path / "layer.geojson.tmp").exists()


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
