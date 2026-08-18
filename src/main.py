"""FastAPI application entry point and lifespan management."""

import asyncio
import os
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from container import (
    get_geo_repo,
    get_history_repo,
    get_job_store,
    get_metrics_repo,
    get_mysql_repo,
    get_singleton_alert_service,
    get_taviso_repo,
)
from controller import alerts, general, intersections, metrics
from db.migrate import ensure_job_migrations, ensure_metrics_migrations
from dependencies import logger, settings
from scheduler import setup_scheduler
from services.alert_job_processor import AlertJobProcessor
from services.metrics_sampler import MetricsSampler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application startup (scheduler init) and shutdown lifecycle."""
    logger.info("Application startup: initializing scheduler ...")

    loop = asyncio.get_running_loop()
    setup_task = asyncio.ensure_future(setup_scheduler(settings, logger))

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, setup_task.cancel)

    try:
        scheduler = await setup_task
    except asyncio.CancelledError:
        logger.warning("Startup aborted by shutdown signal — exiting.")
        raise
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)

    scheduler.start()

    # Pre-warm the render geometry (project dept/prov index to Mercator) now that
    # the scheduler built the caches, so the first alert isn't slowed by it and no
    # cartopy runs in the main process mid-request. Best-effort (swallows errors).
    await get_singleton_alert_service().prewarm_render_geometry()

    geo_repo = get_geo_repo()
    geo_repo.start_eviction_loop()

    # Durable job-history store (always on): migrate the schema, then open it.
    await asyncio.to_thread(ensure_job_migrations, settings)
    job_store = get_job_store()
    await job_store.connect()

    # Processor-metrics store (telemetry; optional): migrate + open only when
    # enabled. Disabled → no sampler; job status/history still work via the store.
    metrics_repo = None
    if settings.metrics_enabled:
        await asyncio.to_thread(ensure_metrics_migrations, settings)
        metrics_repo = get_metrics_repo()
        await metrics_repo.connect()

    # Background worker pool for asynchronous alert generation. Started after the
    # scheduler built the alert caches; stored on app.state (loop-bound lifecycle).
    processor = AlertJobProcessor(
        get_singleton_alert_service(),
        logger,
        maxsize=settings.alerts_job_queue_maxsize,
        workers=settings.alerts_job_workers,
        job_timeout=settings.alerts_job_timeout_seconds,
        supervisor_interval=settings.alerts_supervisor_interval_seconds,
        job_store=job_store,
    )
    processor.start()
    _app.state.alert_job_processor = processor

    # Periodic processor-health sampler (records into the metrics store).
    sampler = None
    if metrics_repo is not None:
        sampler = MetricsSampler(
            processor, get_mysql_repo(), metrics_repo, settings, logger
        )
        sampler.start()

    logger.info("Application startup complete.")

    yield

    logger.info("Application shutdown: stopping scheduler ...")
    scheduler.shutdown()
    logger.info("Scheduler stopped.")

    # Stop the sampler first (it reads the processor + MySQL and writes metrics),
    # then drain in-flight alert jobs before closing DB pools so an in-flight
    # insert never hits a closing connection pool.
    if sampler is not None:
        await sampler.stop()

    await processor.shutdown(drain=True, timeout=settings.alerts_job_shutdown_seconds)

    await geo_repo.stop_eviction_loop()

    get_history_repo().close()
    get_mysql_repo().close()
    # Only close the taviso pool if it was ever instantiated, to avoid opening
    # a connection to the external database just to close it.
    if get_taviso_repo.cache_info().currsize:
        get_taviso_repo().close()
    # Close the local stores last — after the processor drained, so a draining
    # job's record_job still has an open job store.
    if metrics_repo is not None:
        await metrics_repo.close()
    await job_store.close()
    logger.info("Database connections closed.")


app: FastAPI = FastAPI(
    title="alerts-service",
    description="Service that manages alert generation",
    contact={
        "name": "FIUBA TPF Team N°153 Altamirano, Diem, Gismondi, Valeriani",
    },
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let browsers read the ETag of /alerts and /alerts/pending so the
    # visualizer can poll with If-None-Match.
    expose_headers=["ETag"],
)

app.include_router(general.router)
app.include_router(intersections.router)
app.include_router(alerts.router)
app.include_router(metrics.router)

# Serve generated alert GIFs as static files
_output_dir = settings.output_dir
if _output_dir:
    os.makedirs(_output_dir, exist_ok=True)
    app.mount("/alerts", StaticFiles(directory=_output_dir), name="alerts")
