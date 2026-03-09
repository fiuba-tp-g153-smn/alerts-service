# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Alerts Service** — A FastAPI geospatial intersection service that calculates polygon intersections with Argentina's national territory and departments using geographic data from Instituto Geográfico Nacional (IGN).

## Commands

### Development

```bash
make install        # Install dependencies via Poetry
make dev            # Start with Docker Compose + hot reload
make dev-detached   # Start in background
make logs           # Follow container logs
make stop           # Stop containers
make local          # Run natively with uvicorn (requires .env and venv)
```

### Testing

```bash
make test           # Build test image and run pytest with coverage
make test-api       # Run integration tests (requires running service)
```

To run a single test file or test:
```bash
poetry run pytest tests/application/test_basic_endpoints.py -v
poetry run pytest tests/application/test_basic_endpoints.py::test_name -v
```

Test reports output to `reports/` (JUnit XML + HTML coverage).

### Production

```bash
make prod           # Start production stack
make prod-stop      # Stop production
make clean          # Stop and remove volumes
```

## Architecture

### Request Flow

```
POST /intersect-country or /intersect-departments
  → Extract geometry from GeoJSON input (supports Geometry, Feature, FeatureCollection)
  → Load GeoDataFrame from cached .geojson files in ./data/
  → GeoPandas/Shapely intersection computation
  → Return GeoJSON FeatureCollection response
```

### Key Source Files

| File | Role |
|------|------|
| `src/main.py` | FastAPI app entry, CORS middleware |
| `src/controller/general.py` | All geo intersection endpoint logic |
| `src/controller/responses.py` | OpenAPI response schemas |
| `src/settings.py` | Config from environment variables |
| `src/dependencies.py` | Global logger and settings singletons |
| `src/initializers.py` | Logger factory (dev text vs. production NewRelic format) |
| `scripts/download_and_simplify_layers.py` | Async script to fetch and simplify IGN layers |

### Geographic Data

- Downloaded from IGN WFS at startup via `entrypoint.sh` → `scripts/download_and_simplify_layers.py`
- Cached in `./data/` volume: `pais.geojson`, `pais_simple.geojson`, `departamentos.geojson`, `departamentos_simple.geojson`
- Updated monthly via cron job (1st of month, 3 AM)
- Requires env var `OGR_GEOJSON_MAX_OBJ_SIZE=0` for GDAL to handle large files (100+ MB raw)
- Simplified files (~1 MB each) are used by default; full-res (~134 MB) available via `?use_simplified=false`

### Performance

- Simplified mode: ~0.3s per request
- Full resolution: ~10s per request

## Configuration

Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST_PORT` | `8080` | Exposed port |
| `APP_ENV` | `development` | Affects log format (`development` = plain text, `production` = NewRelic JSON) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `SIMPLIFY_TOLERANCE` | `0.01` | Geometry simplification tolerance |
| `COUNTRY_GEOJSON_URL` | IGN WFS URL | Override data source for country layer |
| `DEPARTMENTS_GEOJSON_URL` | IGN WFS URL | Override data source for departments layer |

## Tech Stack

- **Python 3.13.8**, managed via **Poetry**
- **FastAPI** + **Uvicorn** for the web layer
- **GeoPandas**, **Shapely**, **Fiona**, **PyProj** for geospatial operations
- **NewRelic** APM in production
- **Docker** / **Docker Compose** for containerization
- **GitHub Actions** for CI (gitleaks + pytest) and CD (deploy to Coolify)

## Testing Notes

- Tests run with `--disable-socket --allow-hosts=127.0.0.1` (no real network access)
- Formatter: Black (py312 target), imports: isort with black profile
- CI runs on non-main branches; deploy runs on push to `main` after tests pass
