"""Port definition for geo layer download and processing operations."""

import os
from abc import ABC, abstractmethod
from datetime import date


class IGeoLayerProcessor(ABC):
    """Abstract base class for geo layer download and processing operations."""

    # ── Naming helpers (pure, concrete on the base class) ────────────────────

    @staticmethod
    def versioned_key(fname: str) -> str:
        """Return a date-stamped filename, e.g. pais_20260312.geojson."""
        stem, ext = os.path.splitext(fname)
        return f"{stem}_{date.today().strftime('%Y%m%d')}{ext}"

    @staticmethod
    def tolerance_str(tolerance: float) -> str:
        """Encode a tolerance as a filename-safe string. 0.0001 → '0p0001'."""
        return str(tolerance).replace(".", "p")

    @staticmethod
    def tolerance_versioned_key(fname: str, tolerance: float) -> str:
        """Return a tolerance+date-stamped filename.

        Example: pais_simple_L1_T0p0001_20260312.geojson
        """
        stem, ext = os.path.splitext(fname)
        tol = str(tolerance).replace(".", "p")
        return f"{stem}_T{tol}_{date.today().strftime('%Y%m%d')}{ext}"

    # ── I/O operations (abstract) ─────────────────────────────────────────────

    @abstractmethod
    async def download(self, url: str, out_path: str) -> None:
        """Download a URL to a local path."""

    @abstractmethod
    async def simplify(self, in_path: str, out_path: str, tolerance: float) -> None:
        """Simplify a GeoJSON layer with the given tolerance and save to out_path."""
