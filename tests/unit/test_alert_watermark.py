"""Unit tests for the SMN pattern watermark drawn on both alert GIFs.

The real asset is SMN brand material and is NOT in the repo: it is uploaded to
the server by hand and located via WATERMARK_PATH. So these tests build their
own stand-in pattern with the same 472x770 proportions instead of reading it,
and they must keep passing on a clean checkout where the asset is absent.
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

# Native size of the SMN pattern, which the geometry is derived from.
PATTERN_W, PATTERN_H = 472, 770
PATTERN_ASPECT = PATTERN_W / PATTERN_H


@pytest.fixture(name="pattern_file")
def pattern_file_fixture(tmp_path):
    """A stand-in RGBA pattern with the real asset's dimensions."""
    pixels = np.zeros((PATTERN_H, PATTERN_W, 4), dtype=np.uint8)
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


def test_watermark_keeps_the_pattern_proportions(pattern_file):
    """The axes box matches the tiled pattern, so logos are never distorted."""
    bbox = _watermark_axes(pattern_file).get_position()

    box_aspect = (bbox.width * FIG_SIZE[0]) / (bbox.height * FIG_SIZE[1])
    assert abs(box_aspect - worker.WATERMARK_TILE_X * PATTERN_ASPECT) < 1e-6


def test_watermark_covers_the_canvas_width(pattern_file):
    """Two tiles span nearly the whole canvas without overflowing it."""
    bbox = _watermark_axes(pattern_file).get_position()

    assert bbox.width > 0.95
    assert bbox.x0 >= 0
    assert bbox.x1 <= 1.0


def test_watermark_never_reaches_the_phenomenon_band(pattern_file):
    """The watermark stays inside the map band, below the phenomenon strip."""
    bbox = _watermark_axes(pattern_file).get_position()

    assert bbox.y0 > 0
    assert bbox.y1 < worker.MAP_TOP


def test_watermark_is_not_tiled_vertically(pattern_file):
    """The pattern has blank top/bottom rows, so vertical tiling would seam."""
    bbox = _watermark_axes(pattern_file).get_position()

    width_in = bbox.width * FIG_SIZE[0]
    height_in = bbox.height * FIG_SIZE[1]
    assert width_in / height_in > 1, "a vertical tile would make the box taller"


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
