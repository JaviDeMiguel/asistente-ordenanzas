"""Ingesta de ordenanzas: parseo por artículo, vectorización e indexado.

Coordina el flujo de alta de una ordenanza:
  1. Extraer el texto (de texto plano o de un PDF).
  2. Trocear por artículo (`ordinance_parser`); si no se detecta estructura de
     articulado, recurrir a un troceado por palabras.
  3. Vectorizar cada fragmento (incluyendo la etiqueta ordenanza + artículo, de
     modo que la recuperación se beneficie del epígrafe).
  4. Registrar los metadatos en SQLite y los vectores en la base vectorial.
"""

from io import BytesIO

from fastapi import Depends
from pypdf import PdfReader

from app.config import Settings, get_settings
from app.repositories.ordinance_repository import (
    OrdinanceRecord,
    OrdinanceRepository,
    get_ordinance_repository,
)
from app.repositories.vector_store import (
    ArticleVectorStore,
    StoredChunk,
    get_vector_store,
)
from app.services.embedding_service import EmbeddingProvider, get_embedding_provider
from app.services.ordinance_parser import ParsedArticle, parse_ordinance
from app.services.text_utils import clean_boletin_text, split_words


class EmptyOrdinanceError(Exception):
    """El contenido proporcionado no contiene texto utilizable."""


class IngestService:
    """Da de alta ordenanzas troceándolas por artículo e indexándolas."""

    def __init__(
        self,
        repository: OrdinanceRepository,
        vector_store: ArticleVectorStore,
        settings: Settings,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._repository = repository
        self._vectors = vector_store
        self._settings = settings
        self._embeddings = embedding_provider

    def ingest_text(
        self, *, titulo: str, contenido: str, fuente: str | None = None
    ) -> OrdinanceRecord:
        """Trocea, vectoriza y almacena una ordenanza a partir de texto plano."""
        text = contenido.strip()
        if not text:
            raise EmptyOrdinanceError("La ordenanza está vacía.")

        fragments = parse_ordinance(
            text,
            max_words=self._settings.article_max_words,
            overlap=self._settings.article_overlap,
        )
        if not fragments:
            fragments = self._fallback_fragments(text)
        if not fragments:
            raise EmptyOrdinanceError("No se pudo fragmentar la ordenanza.")

        articulo_count = len({frag.numero for frag in fragments})
        record = self._repository.add(
            titulo=titulo,
            fuente=fuente,
            articulo_count=articulo_count,
            chunk_count=len(fragments),
            char_count=len(text),
        )

        embed_texts = [self._embed_text(titulo, frag) for frag in fragments]
        embeddings = self._embeddings.embed_documents(embed_texts)
        chunks = [
            StoredChunk(
                ordenanza_id=record.id,
                ordenanza_titulo=titulo,
                articulo=frag.numero,
                epigrafe=frag.epigrafe,
                titulo=frag.titulo,
                capitulo=frag.capitulo,
                seccion=frag.seccion,
                parte=frag.parte,
                texto=frag.texto,
                embedding=embedding,
            )
            for frag, embedding in zip(fragments, embeddings, strict=True)
        ]
        self._vectors.add(chunks)
        return record

    def ingest_pdf(
        self, *, titulo: str, pdf_bytes: bytes, fuente: str | None = None
    ) -> OrdinanceRecord:
        """Extrae el texto de un PDF y lo da de alta como ordenanza."""
        text = self._extract_pdf_text(pdf_bytes)
        return self.ingest_text(titulo=titulo, contenido=text, fuente=fuente)

    def delete_ordinance(self, ordenanza_id: str) -> bool:
        """Elimina una ordenanza (metadatos + vectores). `True` si existía."""
        if not self._repository.delete(ordenanza_id):
            return False
        self._vectors.delete(ordenanza_id)
        return True

    @staticmethod
    def _embed_text(titulo: str, frag: ParsedArticle) -> str:
        """Texto que se vectoriza: etiqueta (ordenanza + artículo) + cuerpo."""
        etiqueta = f"{titulo}. Artículo {frag.numero}"
        if frag.epigrafe:
            etiqueta += f": {frag.epigrafe}"
        return f"{etiqueta}\n{frag.texto}"

    def _fallback_fragments(self, text: str) -> list[ParsedArticle]:
        """Troceado por palabras cuando no se detecta estructura de articulado."""
        clean = clean_boletin_text(text)
        partes = split_words(
            clean,
            max_words=self._settings.article_max_words,
            overlap=self._settings.article_overlap,
        )
        return [
            ParsedArticle(
                numero=f"frag-{i + 1}",
                epigrafe="(fragmento sin estructura de artículo)",
                titulo="",
                capitulo="",
                seccion="",
                texto=parte,
                parte=0,
                total_partes=1,
            )
            for i, parte in enumerate(partes)
        ]

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        """Extrae el texto de todas las páginas de un PDF."""
        reader = PdfReader(BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()


def get_ingest_service(
    repository: OrdinanceRepository = Depends(get_ordinance_repository),
    vector_store: ArticleVectorStore = Depends(get_vector_store),
    settings: Settings = Depends(get_settings),
) -> IngestService:
    """Dependencia de FastAPI que construye el servicio de ingesta."""
    return IngestService(
        repository=repository,
        vector_store=vector_store,
        settings=settings,
        embedding_provider=get_embedding_provider(settings),
    )
