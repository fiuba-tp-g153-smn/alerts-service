"""Unit tests for the scheduler cache freshness guards (IGN + cuarterón)."""

import os
import pickle

from scheduler import (
    _IGN_CACHE_FORMAT_VERSION,
    _inset_up_to_date,
    _ign_cache_up_to_date,
)


def test_ign_cache_missing_is_stale(tmp_path):
    assert _ign_cache_up_to_date(str(tmp_path / "nope.pkl")) is False


def test_ign_cache_current_version_is_fresh(tmp_path):
    path = tmp_path / "ign.pkl"
    with open(path, "wb") as f:
        pickle.dump({"_format_version": _IGN_CACHE_FORMAT_VERSION, "grupo_a": []}, f)
    assert _ign_cache_up_to_date(str(path)) is True


def test_ign_cache_old_version_is_stale(tmp_path):
    path = tmp_path / "ign.pkl"
    with open(path, "wb") as f:
        pickle.dump({"_format_version": 1, "grupo_a": []}, f)
    assert _ign_cache_up_to_date(str(path)) is False


def test_ign_cache_corrupt_is_stale(tmp_path):
    path = tmp_path / "ign.pkl"
    path.write_bytes(b"not a pickle")
    assert _ign_cache_up_to_date(str(path)) is False


def _touch(path, mtime):
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


def test_cuarteron_missing_png_rebuilds(tmp_path):
    svg = tmp_path / "c.svg"
    svg.write_bytes(b"<svg/>")
    assert _inset_up_to_date(str(tmp_path / "c.png"), str(svg)) is False


def test_cuarteron_png_newer_than_svg_is_fresh(tmp_path):
    png, svg = tmp_path / "c.png", tmp_path / "c.svg"
    _touch(svg, 1000)
    _touch(png, 2000)
    assert _inset_up_to_date(str(png), str(svg)) is True


def test_cuarteron_png_older_than_svg_rebuilds(tmp_path):
    png, svg = tmp_path / "c.png", tmp_path / "c.svg"
    _touch(png, 1000)
    _touch(svg, 2000)
    assert _inset_up_to_date(str(png), str(svg)) is False


def test_cuarteron_svg_missing_keeps_existing_png(tmp_path):
    png = tmp_path / "c.png"
    png.write_bytes(b"x")
    assert _inset_up_to_date(str(png), str(tmp_path / "missing.svg")) is True
