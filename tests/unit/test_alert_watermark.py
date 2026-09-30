"""Unit tests for the SMN pattern watermark drawn on both alert GIFs.

The real asset is SMN brand material and is NOT in the repo: it is uploaded to
the server by hand and located via WATERMARK_PATH. So these tests build their
own stand-in pattern at the asset's authored size instead of reading it, and
they must keep passing on a clean checkout where the asset is absent.
"""

import numpy as np
import pytest
from matplotlib.figure import Figure
from PIL import Image

import alert_generation_worker as worker
from settings import Settings

# Same canvas both generate_*_gif functions build: 13.75x14 in @ 80 dpi.
FIG_SIZE = (13.75, 14)
FIG_DPI = 80
CANVAS_W, CANVAS_H = 1100, 1120

# The asset is authored to fill the map band exactly, so no scaling is needed.
ASSET_W, ASSET_H = 1100, 913


@pytest.fixture(name="pattern_file")
def pattern_file_fixture(tmp_path):
    """A stand-in RGBA pattern at the real asset's authored size."""
    pixels = np.zeros((ASSET_H, ASSET_W, 4), dtype=np.uint8)
    pixels[..., 3] = 40  # faint, like the real pattern's baked-in opacity
    path = tmp_path / "trama_smn.png"
    Image.fromarray(pixels, mode="RGBA").save(path)
    return str(path)


def _watermark_axes(pattern_file):
    """Render a bare figure with only the watermark and return its axes."""
    fig = Figure(figsize=FIG_SIZE, dpi=FIG_DPI)
    worker._add_watermark(fig, pattern_file)  # pylint: disable=protected-access
    axes = fig.axes
    assert len(axes) == 1, "watermark should add exactly one axes"
    return axes[0]


def test_canvas_matches_the_size_the_asset_was_authored_for():
    """The asset is cut to this canvas; a resize here would silently stretch it."""
    assert (FIG_SIZE[0] * FIG_DPI, FIG_SIZE[1] * FIG_DPI) == (CANVAS_W, CANVAS_H)


def test_watermark_covers_the_map_band_edge_to_edge(pattern_file):
    """Full bleed: the whole width, from the bottom up to the phenomenon band."""
    bbox = _watermark_axes(pattern_file).get_position()

    assert (bbox.x0, bbox.y0) == (0.0, 0.0)
    assert bbox.width == 1.0
    assert bbox.height == worker.MAP_TOP


def test_watermark_band_matches_the_asset_pixel_size(pattern_file):
    """The band the watermark fills is the size the asset was designed at."""
    bbox = _watermark_axes(pattern_file).get_position()

    assert round(bbox.width * CANVAS_W) == ASSET_W
    assert round(bbox.height * CANVAS_H) == ASSET_H


def test_watermark_never_overlaps_the_phenomenon_band(pattern_file):
    """Full bleed stops at MAP_TOP, leaving header and phenomenon untouched."""
    bbox = _watermark_axes(pattern_file).get_position()

    assert bbox.y1 <= worker.MAP_TOP
    assert bbox.y1 < worker.PHENOM_Y + worker.PHENOM_H


def test_watermark_is_not_rescaled(pattern_file):
    """aspect='auto' fills the band; 'equal' would letterbox it instead."""
    assert _watermark_axes(pattern_file).get_aspect() == "auto"


def test_missing_asset_skips_the_watermark_instead_of_failing(tmp_path):
    """The asset is absent on a clean checkout: warn and render without it."""
    fig = Figure(figsize=FIG_SIZE, dpi=FIG_DPI)
    worker._add_watermark(  # pylint: disable=protected-access
        fig, str(tmp_path / "does_not_exist.png")
    )
    assert not fig.axes


def test_worker_default_path_points_at_the_asset_directory():
    """The payload fallback keeps working for callers that omit the path."""
    assert worker.DEFAULT_WATERMARK_PATH.endswith("/data_alerts/trama_smn.png")


def test_settings_reads_watermark_path_from_the_environment(monkeypatch):
    """Production locates the hand-uploaded asset through WATERMARK_PATH."""
    monkeypatch.setenv("WATERMARK_PATH", "/app/assets/trama_smn.png")
    assert Settings().watermark_path == "/app/assets/trama_smn.png"


def test_settings_falls_back_to_the_baked_in_path(monkeypatch):
    """Without the env var, dev keeps using the bind-mounted data_alerts copy."""
    monkeypatch.delenv("WATERMARK_PATH", raising=False)
    assert Settings().watermark_path == worker.DEFAULT_WATERMARK_PATH
