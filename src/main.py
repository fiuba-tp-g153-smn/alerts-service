from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller import general
from dependencies import logger, settings
from scheduler import setup_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = await setup_scheduler(settings, logger)
    scheduler.start()
    yield
    scheduler.shutdown()


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
    allow_origins=["*"],  # Allow all origins - can be restricted to ["http://localhost:4200"] if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(general.router)
