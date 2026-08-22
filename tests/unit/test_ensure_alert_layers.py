"""Resilience tests for _ensure_alert_layers (BUG-03 extended to the alert-layer path)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from scheduler import _ensure_alert_layers

# _retry_async (in geo_layer_sync_service) does the sleeping — patch it there so the
# retry/backoff tests don't incur real delays.
_SLEEP_PATH = "services.geo_layer_sync_service.asyncio.sleep"


def _settings(tmp_path):
    settings = MagicMock()
    settings.data_dir = str(tmp_path)
    settings.alerts_detail_level = 7
    settings.detail_level_tolerances = {7: 0.005}
    settings.provinces_geojson_url = "http://example.com/prov.geojson"
    return settings


async def test_ensure_alert_layers_download_failure_is_non_fatal(tmp_path):
    settings = _settings(tmp_path)
    logger = MagicMock()
    processor = AsyncMock()
    processor.download.side_effect = RuntimeError("IGN 504")

    with patch(_SLEEP_PATH, new=AsyncMock()):
        await _ensure_alert_layers(settings, logger, processor)  # must not raise

    assert processor.download.await_count == 3  # retried before giving up
    processor.simplify.assert_not_awaited()
    logger.error.assert_called()


async def test_ensure_alert_layers_retries_download_then_succeeds(tmp_path):
    settings = _settings(tmp_path)
    logger = MagicMock()
    processor = AsyncMock()
    attempts = {"n": 0}

    async def flaky_download(_url, path):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("504")
        Path(path).write_text("{}", encoding="utf-8")

    processor.download.side_effect = flaky_download

    with patch(_SLEEP_PATH, new=AsyncMock()):
        await _ensure_alert_layers(settings, logger, processor)

    assert attempts["n"] == 2  # failed once, succeeded on retry
    processor.simplify.assert_awaited()


async def test_ensure_alert_layers_cleans_raw_tmp_on_simplify_failure(tmp_path):
    settings = _settings(tmp_path)
    logger = MagicMock()
    processor = AsyncMock()

    async def fake_download(_url, path):
        Path(path).write_text("{}", encoding="utf-8")

    processor.download.side_effect = fake_download
    processor.simplify.side_effect = RuntimeError("simplify failed")

    with patch(_SLEEP_PATH, new=AsyncMock()):
        await _ensure_alert_layers(settings, logger, processor)  # non-fatal

    assert not (tmp_path / "provincias_raw_tmp.geojson").exists()  # finally cleaned it
    logger.error.assert_called()
