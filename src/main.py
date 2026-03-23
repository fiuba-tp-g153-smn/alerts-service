"""FastAPI application entry point and lifespan management."""

import asyncio
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from container import get_geo_repo
from controller import general, intersections
from dependencies import logger, settings
from scheduler import setup_scheduler


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

    geo_repo = get_geo_repo()
    geo_repo.start_eviction_loop()

    logger.info("Application startup complete.")

    yield

    logger.info("Application shutdown: stopping scheduler ...")
    scheduler.shutdown()
    logger.info("Scheduler stopped.")

    await geo_repo.stop_eviction_loop()


app: FastAPI = FastAPI(
    title="alerts-service",
    description="Servicio que maneja la gestión de alertas",
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
)

app.include_router(general.router)
app.include_router(intersections.router)
