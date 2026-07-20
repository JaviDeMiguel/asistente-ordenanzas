"""Esquemas Pydantic para validar las entradas y salidas de la API."""

from datetime import datetime

from pydantic import BaseModel, Field

# --- Tablas ------------------------------------------------------------------

class TableRow(BaseModel):
    """Una fila de una tabla: una clave y sus valores alineados con las columnas."""

    clave: str = Field(
        ...,
        min_length=1,
        description="Etiqueta de la fila (p. ej. 'Uso residencial').",
    )
    valores: list[str] = Field(
        ...,
        min_length=1,
        description="Valores de la fila, en el mismo orden que `columnas`.",
    )


class TableSpec(BaseModel):
    """Tabla estructurada asociada a un artículo de una ordenanza.

    Las tablas de las ordenanzas del BOP suelen ir **incrustadas como imágenes**,
    por lo que no se pueden extraer del PDF con herramientas de texto. Se ingieren
    de forma estructurada (valores verificados) y se linealizan a filas citables.
    """

    tabla: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Etiqueta de la tabla (p. ej. 'Tabla 1').",
    )
    articulo: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Número del artículo al que pertenece la tabla (p. ej. '10').",
    )
    descripcion: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Descripción / título de la tabla.",
    )
    columnas: list[str] = Field(
        ...,
        min_length=1,
        description="Etiquetas de las columnas de valores (p. ej. 'Ln (noche)').",
    )
    unidad: str = Field(
        default="",
        max_length=30,
        description="Unidad de los valores (p. ej. 'dB'), opcional.",
    )
    filas: list[TableRow] = Field(..., min_length=1)


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
    tablas: list[TableSpec] = Field(
        default_factory=list,
        description="Tablas estructuradas de la ordenanza (opcional).",
    )


class OrdinanceSummary(BaseModel):
    """Metadatos de una ordenanza indexada."""

    id: str
    titulo: str
    fuente: str | None = None
    articulo_count: int = Field(..., description="Número de artículos detectados.")
    tabla_count: int = Field(0, description="Número de tablas estructuradas indexadas.")
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
    """Fragmento de un artículo (o de una tabla) usado como fuente."""

    ordenanza_id: str
    ordenanza_titulo: str
    tipo: str = Field("articulo", description="Tipo de fuente: 'articulo' o 'tabla'.")
    articulo: str = Field(..., description="Número del artículo, p. ej. '23'.")
    tabla: str = Field("", description="Etiqueta de la tabla, si la fuente es una tabla.")
    epigrafe: str = Field("", description="Título del artículo o descripción de la tabla.")
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
