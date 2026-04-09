"""Pre-download Natural Earth 50m shapefiles so they are cached in the Docker image."""

import cartopy.io.shapereader as sr

DATASETS = [
    ("physical", "coastline"),
    ("physical", "land"),
    ("physical", "ocean"),
    ("cultural", "admin_0_boundary_lines_land"),
]

for category, name in DATASETS:
    path = sr.natural_earth("50m", category, name)
    list(sr.Reader(path).geometries())
    print(f"  cached: 50m/{category}/{name}")

print("Natural Earth 50m cache complete")
