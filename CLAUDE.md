# CLAUDE.md

## Collaboration Protocol

1. **Before coding**: Describe approach → wait for approval. Ask clarifying questions if requirements are ambiguous.
2. **>3 file changes**: Stop. Break into smaller tasks first.
3. **After coding**: List what could break and which tests need adding/updating.

## Commands

```bash
make install     # Install deps (Poetry 2.0+)
make dev         # Docker dev (hot reload)
make test        # Tests in Docker
make precommit   # Pre-commit hooks (pylint, mypy, black)
make local       # Run without Docker

# Single test file/function:
docker compose -f docker-compose.yaml run --rm alerts-service poetry run pytest tests/unit/test_geo_utils.py -v
docker compose -f docker-compose.yaml run --rm alerts-service poetry run pytest tests/unit/test_geo_utils.py::test_function_name -v
```

## Architecture

FastAPI geospatial intersection service (Python). Given a GeoJSON polygon, computes intersections against Argentina's country boundary and departments sourced from IGN.

### Request Flow

```
POST /intersect/country or /intersect/departments  (detail_level 1-5)
  → Controller (controller/intersections.py)
  → GeoIntersectionService (services/geo_intersection_service.py)
      └─ cached GeoDataFrame (per detail_level) → shapely intersection → GeoJSON
  → FileSystemGeoLayerRepository (adapters/geo_layer_repository.py) provides layer data
```

### Startup & Background Jobs

On startup (`main.py` lifespan), the scheduler (`scheduler/__init__.py`) runs S3 reconciliation: compares local `data/` files against S3 by date-stamp, downloads missing layers or re-generates from IGN. APScheduler cron (default: weekly Sunday 3 AM UTC) refreshes layers — download → simplify (one GeoJSON per detail_level) → upload to S3. History saved to `data/history.db` (SQLite).

### Key Design Decisions

- **Hexagonal architecture**: interfaces in `ports/`, implementations in `adapters/`, business logic in `services/`.
- **Pre-simplified layers**: each `detail_level` (1-5, plus internal 7 for alerts) is a date-stamped GeoJSON simplified at a fixed tolerance, loaded into an in-memory GeoDataFrame cache. Higher `detail_level` = more detail (lower tolerance).
- **Versioned files**: date-stamped per-level layers (e.g., `pais_simple_L5_T0p01_20260314.geojson`); glob patterns locate the latest version.
- **DI via container**: `container.py` provides singletons and per-request services via FastAPI `Depends`.

### Key Source Files

| File | Role |
|---|---|
| `src/main.py` | App entry, lifespan, router registration |
| `src/services/geo_intersection_service.py` | Core intersection logic (per detail_level) |
| `src/adapters/geo_layer_repository.py` | Loads/caches versioned GeoJSON from `data/` |
| `src/adapters/sqlite_history.py` | SQLite persistence for layer refresh history |
| `src/adapters/s3_storage.py` | S3 backup/restore for layer files |
| `src/scheduler/__init__.py` | S3 reconciliation + APScheduler cron registration |
| `src/services/layer_refresh_service.py` | Download → simplify (per detail_level) pipeline |
| `src/container.py` | FastAPI dependency injection wiring |

## Configuration

Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `production` enables NewRelic JSON logging |
| `detail_levels` (settings.json) | see file | Per-level simplify tolerance; API exposes 1-5, level 7 is internal (alerts) |
| `alert_detail_level` (settings.json) | `7` | detail_level used for alert generation (internal, not API-selectable) |
| `layer_update_cron` (settings.json) | `0 3 * * 0` | Cron for layer refresh |
| `S3_ENDPOINT` / `S3_BUCKET_NAME` | (empty) | Required for S3 backup |
| `COUNTRY_GEOJSON_URL` / `DEPARTMENTS_GEOJSON_URL` | IGN WFS URLs | Override data source |

New config → add to settings with a sensible default. Don't scatter `os.getenv()` — centralize in config module.

API docs at `http://localhost:8080/docs` when running.

## Engineering Rules

### FastAPI Conventions

- All route handlers must be `async def`. Wrap blocking I/O with `asyncio.to_thread()`.
- Use `Depends()` for shared logic — prefer DI via `container.py` over importing singletons directly in routes.
- Type all endpoints: `response_model`, status codes, Pydantic models. Never return raw dicts when a model exists.
- **Controllers handle HTTP concerns only** — no business logic. Services return domain results or raise domain exceptions, **never `HTTPException`**. Controllers translate to HTTP status codes.
- Use `lifespan` pattern only — never deprecated `@app.on_event`.

### Code Style

- Early returns; functions <20 lines; one class per file.
- `handle_` prefix for event handlers; verb-noun naming.
- Immutable by default: `frozen=True`, `slots=True` dataclasses for data containers.
- Fail fast: validate inputs early, domain-specific exceptions, no bare `except`.
- **Minimal changes**: only modify code directly related to the task.

### Design Principles

- **Hexagonal boundaries**: `ports/` define interfaces (ABC/Protocol), `adapters/` implement them, `services/` contain business logic. Never import adapters directly from services — depend on port abstractions.
- **DI via constructor**: Pass deps through `__init__`. `container.py` wires everything. No service locator, no hard-importing concrete clients.
- **Composition over inheritance**: Prefer has-a over is-a.
- **Open/Closed**: New layer types or data sources → new adapter implementing the port. Don't add conditionals to existing services.
- **Keep interfaces small** (ISP): Many small protocols over one large interface.

### Testing

SIFER principles: **S**imple, **I**solated, **F**ast, **E**xplicit, **R**epresentative.

- `tests/unit/` — fast, no network/Docker. `tests/application/` — integration against running service.
- Test interfaces, not implementations — tests should work with any conforming impl.
- Mock only external dependencies (S3, IGN WFS, network). **Never mock own models, dataclasses, or utility functions** — use real instances.
- Use DI to make mocking easy. Use Protocol for lightweight test doubles.

## Resource Management

### Memory (Critical — GeoPandas/Shapely)

- **Subprocess isolation for heavy GeoPandas is load-bearing** — GeoPandas causes glibc arena memory bloat. Keep heavy ops (layer simplification in `geo_processing_worker.py`, alert rendering in `alert_generation_worker.py`) in subprocesses; never move them into the main process.
- Stream large files (generators / async iteration); context managers for all cleanup.
- Bounded buffers: `asyncio.Queue(maxsize=N)` where applicable.
- The in-memory GeoDataFrame cache holds only the pre-simplified per-level layers; raw full-resolution geometry is never loaded in the main process.

### Concurrency

- `asyncio` for I/O-bound; subprocesses for CPU-bound geo operations.
- `asyncio.Semaphore(N)` to bound concurrent ops — no unbounded task creation.
- Never use blocking I/O in async functions (use `asyncio.to_thread`).
- Connection pooling for HTTP sessions and S3 clients.
- Batch small operations; lazy evaluation for expensive computations.

### Infrastructure

- Docker: set `mem_limit` and `cpus` to prevent runaway geo processing.
- S3: aioboto3 async, exponential backoff retries, stream to disk for large layers.
- Monitoring: structured logging with timing data, `time.perf_counter()` for measurements.

## Anti-Patterns

- ❌ God objects, circular deps, global mutable state
- ❌ Business logic in controllers or adapters
- ❌ Importing concrete adapters from services (bypass ports)
- ❌ Full-res geo processing in main process (memory bloat)
- ❌ Unbounded async task / subprocess creation (use semaphores)
- ❌ Blocking I/O in async functions (use `asyncio.to_thread`)
- ❌ Catching `Exception` without re-raise or proper handling
- ❌ Not cleaning up resources in error paths
- ❌ Mocking own models/dataclasses in tests
