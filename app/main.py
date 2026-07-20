"""Punto de entrada del Asistente de Ordenanzas Municipales.

Ejecutar en desarrollo con:
    uvicorn app.main:app --reload
Documentación interactiva disponible en /docs (Swagger) y /redoc.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.db import get_database
from app.routers import consultas, ordenanzas


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Inicializa la base de datos (crea las tablas si no existen) al arrancar."""
    get_database()
    yield


app = FastAPI(
    title="Asistente de Ordenanzas Municipales",
    description=(
        "API de tipo RAG sobre ordenanzas municipales: sube el texto (o el PDF) "
        "de una ordenanza y pregunta en lenguaje natural. El texto se trocea "
        "**por artículo**, la recuperación es por similitud de embeddings y las "
        "respuestas las genera Claude **citando el artículo y la ordenanza** en "
        "los que se basa."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.include_router(ordenanzas.router)
app.include_router(consultas.router)


@app.get("/health", tags=["salud"], summary="Comprobación de estado")
def health() -> dict[str, str]:
    """Endpoint sencillo para verificar que el servicio está en marcha."""
    return {"status": "ok", "version": __version__}
