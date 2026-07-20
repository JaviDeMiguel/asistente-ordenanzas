"""Consulta RAG sobre las ordenanzas: recuperación + respuesta citada.

Coordina las dos piezas de la consulta:
  1. Recuperación: vectorizar la pregunta y traer los `top_k` artículos más
     similares (opcionalmente acotados a una ordenanza).
  2. Generación: pasar esos artículos —etiquetados con su ordenanza y número—
     al LLM, que responde citando el artículo y la ordenanza.

También expone una búsqueda pura (sin LLM) para inspeccionar qué artículos
recupera el sistema.
"""

from fastapi import Depends

from app.config import Settings, get_settings
from app.models.schemas import AnswerResponse, Cita, SearchResponse
from app.repositories.ordinance_repository import (
    OrdinanceRepository,
    get_ordinance_repository,
)
from app.repositories.vector_store import (
    ArticleHit,
    ArticleVectorStore,
    get_vector_store,
)
from app.services.embedding_service import EmbeddingProvider, get_embedding_provider
from app.services.llm_service import LLMService

_SIN_RESULTADOS = (
    "No se han encontrado artículos relevantes en las ordenanzas indexadas para "
    "esta pregunta."
)


class OrdinanceNotFoundError(Exception):
    """La ordenanza indicada como filtro no existe."""


class QAService:
    """Responde preguntas sobre las ordenanzas aplicando RAG con citas."""

    def __init__(
        self,
        repository: OrdinanceRepository,
        vector_store: ArticleVectorStore,
        settings: Settings,
        llm_service: LLMService,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._repository = repository
        self._vectors = vector_store
        self._settings = settings
        self._llm = llm_service
        self._embeddings = embedding_provider

    def answer(
        self,
        pregunta: str,
        ordenanza_id: str | None = None,
        top_k: int | None = None,
    ) -> AnswerResponse:
        """Responde una pregunta recuperando artículos y generando la respuesta."""
        hits = self._retrieve(pregunta, ordenanza_id, top_k)
        if not hits:
            return AnswerResponse(
                pregunta=pregunta, respuesta=_SIN_RESULTADOS, fuentes=[]
            )

        contexto = [self._context_block(hit) for hit in hits]
        self._llm.ensure_configured()  # falla pronto (503) si no hay clave
        respuesta = self._llm.answer(pregunta=pregunta, contexto=contexto)
        return AnswerResponse(
            pregunta=pregunta,
            respuesta=respuesta,
            fuentes=[self._to_cita(hit) for hit in hits],
        )

    def search(
        self,
        consulta: str,
        ordenanza_id: str | None = None,
        top_k: int | None = None,
    ) -> SearchResponse:
        """Recupera los artículos más relevantes sin generar respuesta."""
        hits = self._retrieve(consulta, ordenanza_id, top_k)
        return SearchResponse(
            consulta=consulta, resultados=[self._to_cita(hit) for hit in hits]
        )

    def _retrieve(
        self, consulta: str, ordenanza_id: str | None, top_k: int | None
    ) -> list[ArticleHit]:
        """Valida el filtro, vectoriza la consulta y recupera los artículos."""
        if ordenanza_id is not None and self._repository.get(ordenanza_id) is None:
            raise OrdinanceNotFoundError(ordenanza_id)
        limit = top_k or self._settings.top_k
        query_vector = self._embeddings.embed_query(consulta)
        return self._vectors.query(query_vector, limit, ordenanza_id)

    @staticmethod
    def _context_block(hit: ArticleHit) -> str:
        """Bloque de contexto etiquetado que ve el LLM (para poder citar)."""
        cabecera = [hit.ordenanza_titulo]
        if hit.tipo == "tabla":
            etiqueta = f"{hit.tabla} (Artículo {hit.articulo})"
            if hit.epigrafe:
                etiqueta += f" — {hit.epigrafe}"
            cabecera.append(etiqueta)
        else:
            cabecera.extend(
                part for part in (hit.titulo, hit.capitulo, hit.seccion) if part
            )
            etiqueta = f"Artículo {hit.articulo}"
            if hit.epigrafe:
                etiqueta += f" — {hit.epigrafe}"
            cabecera.append(etiqueta)
        return f"[{' · '.join(cabecera)}]\n{hit.texto}"

    @staticmethod
    def _to_cita(hit: ArticleHit) -> Cita:
        return Cita(
            ordenanza_id=hit.ordenanza_id,
            ordenanza_titulo=hit.ordenanza_titulo,
            tipo=hit.tipo,
            articulo=hit.articulo,
            tabla=hit.tabla,
            epigrafe=hit.epigrafe,
            titulo=hit.titulo,
            capitulo=hit.capitulo,
            seccion=hit.seccion,
            texto=hit.texto,
            score=hit.score,
        )


def get_qa_service(
    repository: OrdinanceRepository = Depends(get_ordinance_repository),
    vector_store: ArticleVectorStore = Depends(get_vector_store),
    settings: Settings = Depends(get_settings),
) -> QAService:
    """Dependencia de FastAPI que construye el servicio de consulta."""
    return QAService(
        repository=repository,
        vector_store=vector_store,
        settings=settings,
        llm_service=LLMService(settings),
        embedding_provider=get_embedding_provider(settings),
    )
