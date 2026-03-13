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
| `src/scheduler/__init__.py` | Startup layer reconciliation (FS/S3 bidirectional sync) + cron scheduler |
| `src/services/layer_refresh_service.py` | Full refresh cycle: download → simplify → FGB → upload to S3 |

### Geographic Data

- On startup, `src/scheduler/__init__.py` reconciles local `./data/` against S3 by date-stamp; downloads missing files from S3 or re-generates from IGN if neither exists
- Updated monthly via cron job (1st of month, 3 AM) via `layer_refresh_service.py`
- 4 permanent files per refresh cycle: `pais_simple_YYYYMMDD.geojson`, `departamentos_simple_YYYYMMDD.geojson`, `pais_YYYYMMDD.fgb`, `departamentos_YYYYMMDD.fgb`
- Raw full-res GeoJSON (~134 MB each) is downloaded to a temp path during refresh and deleted after processing — never persisted
- Requires env var `OGR_GEOJSON_MAX_OBJ_SIZE=0` for GDAL to handle large files
- Simplified files (~1 MB each) are used by default; full-res via FlatGeobuf available via `?use_simplified=false`

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

- **Python 3.13.12**, managed via **Poetry**
- **FastAPI** + **Uvicorn** for the web layer
- **GeoPandas**, **Shapely**, **Fiona**, **PyProj** for geospatial operations
- **NewRelic** APM in production
- **Docker** / **Docker Compose** for containerization
- **GitHub Actions** for CI (gitleaks + pytest) and CD (deploy to Coolify)

## Testing Notes

- Tests run with `--disable-socket --allow-hosts=127.0.0.1` (no real network access)
- Formatter: Black (py312 target), imports: isort with black profile
- CI runs on non-main branches; deploy runs on push to `main` after tests pass
