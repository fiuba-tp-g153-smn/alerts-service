"""FastAPI application entry point and lifespan management."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from container import get_batch_manager
from controller import general, intersections
from dependencies import logger, settings
from scheduler import setup_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application startup (scheduler init) and shutdown lifecycle."""
    logger.info("Application startup: initializing scheduler ...")

    scheduler = await setup_scheduler(settings, logger)
    scheduler.start()

    logger.info("Application startup: initializing batch manager ...")
    batch_manager = get_batch_manager()

    logger.info("Application startup complete.")

    yield

    logger.info("Application shutdown: stopping scheduler ...")
    scheduler.shutdown()
    logger.info("Scheduler stopped.")

    logger.info("Application shutdown: stopping batch manager ...")
    batch_manager.stop()
    logger.info("Batch manager stopped.")


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
