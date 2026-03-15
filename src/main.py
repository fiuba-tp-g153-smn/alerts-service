"""FastAPI application entry point and lifespan management."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from container import get_geo_repo
from controller import alerts, general, intersections
from dependencies import logger, settings
from scheduler import setup_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application startup (scheduler init) and shutdown lifecycle."""
    logger.info("Application startup: initializing scheduler ...")

    scheduler = await setup_scheduler(settings, logger)
    scheduler.start()

    logger.info("Application startup: preloading simplified geo layers ...")
    geo_repo = get_geo_repo()
    geo_repo.preload()

    logger.info("Application startup complete.")

    yield

    logger.info("Application shutdown: stopping scheduler ...")
    scheduler.shutdown()
    logger.info("Scheduler stopped.")


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
app.include_router(alerts.router)

# Serve generated alert GIFs as static files
_output_dir = settings.output_dir
os.makedirs(_output_dir, exist_ok=True)
app.mount("/alerts", StaticFiles(directory=_output_dir), name="alerts")

