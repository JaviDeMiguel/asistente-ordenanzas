"""Esquemas Pydantic para validar las entradas y salidas de la API."""

from datetime import datetime

from pydantic import BaseModel, Field

# --- Ingesta de ordenanzas ---------------------------------------------------

class OrdinanceIngestRequest(BaseModel):
    """Cuerpo para dar de alta una ordenanza a partir de texto plano."""

    titulo: str = Field(
        ...,
        min_length=3,
        max_length=300,
        description="Título de la ordenanza (p. ej. 'Ordenanza de Tráfico').",
    )
    contenido: str = Field(
        ...,
        min_length=1,
        max_length=2_000_000,
        description="Texto completo de la ordenanza (articulado).",
    )
    fuente: str | None = Field(
        default=None,
        max_length=300,
        description="Referencia de publicación (p. ej. 'BOP Toledo nº 45, 25/02/2013').",
    )


class OrdinanceSummary(BaseModel):
    """Metadatos de una ordenanza indexada."""

    id: str
    titulo: str
    fuente: str | None = None
    articulo_count: int = Field(..., description="Número de artículos detectados.")
    chunk_count: int = Field(..., description="Número de fragmentos indexados.")
    char_count: int = Field(..., description="Longitud del texto en caracteres.")
    created_at: datetime


# --- Consulta / Respuesta (RAG) ----------------------------------------------

class QuestionRequest(BaseModel):
    """Pregunta en lenguaje natural sobre las ordenanzas."""

    pregunta: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Pregunta en lenguaje natural.",
    )
    ordenanza_id: str | None = Field(
        default=None,
        description="Si se indica, restringe la búsqueda a esa ordenanza.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Nº de artículos a recuperar (por defecto, el de la config).",
    )


class Cita(BaseModel):
    """Fragmento de un artículo usado como fuente de la respuesta."""

    ordenanza_id: str
    ordenanza_titulo: str
    articulo: str = Field(..., description="Número del artículo, p. ej. '23'.")
    epigrafe: str = Field("", description="Título del artículo.")
    titulo: str = Field("", description="Título (jerárquico) contenedor.")
    capitulo: str = Field("", description="Capítulo contenedor.")
    seccion: str = Field("", description="Sección contenedora.")
    texto: str
    score: float = Field(..., description="Similitud (coseno) frente a la pregunta.")


class AnswerResponse(BaseModel):
    """Respuesta generada por el asistente junto con sus citas."""

    pregunta: str
    respuesta: str
    fuentes: list[Cita] = Field(
        default_factory=list,
        description="Artículos recuperados que fundamentan la respuesta.",
    )


class SearchResponse(BaseModel):
    """Resultados de una búsqueda de artículos (sin generar respuesta)."""

    consulta: str
    resultados: list[Cita] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Forma estándar de los errores devueltos por la API."""

    detail: str
