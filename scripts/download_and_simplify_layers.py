"""Standalone script to download and simplify IGN WFS GeoJSON layers into ./data/."""

# Standard library imports
import asyncio
import os

# Third-party imports
import aiohttp
import geopandas as gpd

# Set environment variable to allow large GeoJSON files (in MB, 0 = unlimited)
os.environ["OGR_GEOJSON_MAX_OBJ_SIZE"] = "0"

IGN_BASE_URL = "https://wms.ign.gob.ar/geoserver/ows"
COUNTRY_GEOJSON_URL = (
    f"{IGN_BASE_URL}?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=ign:pais&outputFormat=application/json"
)
DEPARTMENTS_GEOJSON_URL = (
    f"{IGN_BASE_URL}?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=ign:departamento&outputFormat=application/json"
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
# Get tolerance from environment variable, default to 0.01 (1% simplification)
SIMPLIFY_TOLERANCE = float(os.environ.get("SIMPLIFY_TOLERANCE", "0.01"))


async def download_geojson_async(url, out_path):
    """Download GeoJSON from url and write it to out_path."""
    print(f"Downloading {url} ...")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            content = await resp.read()
            with open(out_path, "wb") as f:
                f.write(content)
    print(f"Saved to {out_path}")


async def simplify_geojson_async(in_path, out_path, tolerance=SIMPLIFY_TOLERANCE):
    """Read in_path, simplify geometries with the given tolerance, and write to out_path."""
    print(f"Simplifying {in_path} ...")

    # Run blocking geopandas in a thread
    def _simplify():
        gdf = gpd.read_file(in_path)
        gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
        gdf.to_file(out_path, driver="GeoJSON")
        print(f"Simplified and saved to {out_path}")

    await asyncio.to_thread(_simplify)


async def main_async():
    """Download raw layers from IGN and generate simplified versions in DATA_DIR."""
    os.makedirs(DATA_DIR, exist_ok=True)
    country_path = os.path.join(DATA_DIR, "pais.geojson")
    country_simple_path = os.path.join(DATA_DIR, "pais_simple.geojson")
    deptos_path = os.path.join(DATA_DIR, "departamentos.geojson")
    deptos_simple_path = os.path.join(DATA_DIR, "departamentos_simple.geojson")

    # Download in parallel if needed
    download_tasks = []
    if not os.path.exists(country_path):
        download_tasks.append(download_geojson_async(COUNTRY_GEOJSON_URL, country_path))
    if not os.path.exists(deptos_path):
        download_tasks.append(
            download_geojson_async(DEPARTMENTS_GEOJSON_URL, deptos_path)
        )
    if download_tasks:
        await asyncio.gather(*download_tasks)

    # Generate simplified versions (1% tolerance)
    print(f"Generating simplified versions with {SIMPLIFY_TOLERANCE*100}% tolerance...")
    await asyncio.gather(
        simplify_geojson_async(country_path, country_simple_path),
        simplify_geojson_async(deptos_path, deptos_simple_path),
    )


if __name__ == "__main__":
    asyncio.run(main_async())
