"""Almacén vectorial de artículos basado en ChromaDB.

ChromaDB actúa como **base de datos vectorial**: guarda cada fragmento de
artículo junto a su embedding y realiza la búsqueda por vecinos más cercanos
con un índice HNSW (distancia de coseno).

A diferencia de un RAG genérico, aquí cada fragmento lleva metadatos ricos del
articulado —número de artículo, epígrafe, Título/Capítulo/Sección y ordenanza—
de modo que la respuesta pueda **citar** «Artículo 23 de la Ordenanza de ITE»
en lugar de devolver un fragmento anónimo.

Los vectores se calculan con nuestros proveedores (`EmbeddingProvider`: local o
Voyage); Chroma se usa como puro almacén + índice, por lo que los embeddings se
pasan ya calculados.
"""

from dataclasses import dataclass

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import Settings, get_settings

# Desactivamos la telemetría anónima de Chroma (evita conexiones en segundo plano).
_CHROMA_SETTINGS = ChromaSettings(anonymized_telemetry=False)


@dataclass
class StoredChunk:
    """Fragmento (de artículo o de tabla) con su embedding y sus metadatos."""

    ordenanza_id: str
    ordenanza_titulo: str
    articulo: str
    epigrafe: str
    titulo: str
    capitulo: str
    seccion: str
    parte: int
    texto: str
    embedding: list[float]
    tipo: str = "articulo"  # "articulo" | "tabla"
    tabla: str = ""


@dataclass
class ArticleHit:
    """Fragmento (artículo o tabla) recuperado en una búsqueda por similitud."""

    ordenanza_id: str
    ordenanza_titulo: str
    articulo: str
    epigrafe: str
    titulo: str
    capitulo: str
    seccion: str
    texto: str
    score: float
    tipo: str = "articulo"
    tabla: str = ""


class ArticleVectorStore:
    """Operaciones vectoriales sobre los artículos (respaldadas por Chroma)."""

    def __init__(self, settings: Settings) -> None:
        self._name = settings.chroma_collection
        if settings.chroma_path == ":memory:":
            self._client = chromadb.EphemeralClient(settings=_CHROMA_SETTINGS)
        else:
            self._client = chromadb.PersistentClient(
                path=settings.chroma_path, settings=_CHROMA_SETTINGS
            )
        self._collection = self._get_or_create()

    def _get_or_create(self):
        # Espacio de coseno: como los vectores están normalizados (norma L2 = 1),
        # es equivalente al producto escalar.
        return self._client.get_or_create_collection(
            name=self._name,
            configuration={"hnsw": {"space": "cosine"}},
        )

    def add(self, chunks: list[StoredChunk]) -> None:
        """Indexa los fragmentos de artículos con sus embeddings."""
        if not chunks:
            return
        self._collection.add(
            # El id incluye tipo y tabla para que una fila de tabla asociada al
            # artículo N no colisione con el propio artículo N.
            ids=[
                f"{c.ordenanza_id}:{c.tipo}:{c.tabla or c.articulo}:{c.parte}"
                for c in chunks
            ],
            embeddings=[c.embedding for c in chunks],
            documents=[c.texto for c in chunks],
            metadatas=[
                {
                    "ordenanza_id": c.ordenanza_id,
                    "ordenanza_titulo": c.ordenanza_titulo,
                    "tipo": c.tipo,
                    "articulo": c.articulo,
                    "tabla": c.tabla,
                    "epigrafe": c.epigrafe,
                    "titulo": c.titulo,
                    "capitulo": c.capitulo,
                    "seccion": c.seccion,
                    "parte": c.parte,
                }
                for c in chunks
            ],
        )

    def query(
        self,
        query_embedding: list[float],
        top_k: int,
        ordenanza_id: str | None = None,
    ) -> list[ArticleHit]:
        """Devuelve los `top_k` artículos más similares a la consulta.

        Si `ordenanza_id` no es `None`, restringe la búsqueda a esa ordenanza.
        La similitud se expresa como `score = 1 - distancia_coseno` (1.0 =
        idéntico).
        """
        where = {"ordenanza_id": ordenanza_id} if ordenanza_id else None
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )

        ids = result["ids"][0]
        if not ids:
            return []

        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        return [
            ArticleHit(
                ordenanza_id=str(meta["ordenanza_id"]),
                ordenanza_titulo=str(meta["ordenanza_titulo"]),
                articulo=str(meta["articulo"]),
                epigrafe=str(meta.get("epigrafe", "")),
                titulo=str(meta.get("titulo", "")),
                capitulo=str(meta.get("capitulo", "")),
                seccion=str(meta.get("seccion", "")),
                texto=texto,
                score=1.0 - float(distance),
                tipo=str(meta.get("tipo", "articulo")),
                tabla=str(meta.get("tabla", "")),
            )
            for texto, meta, distance in zip(
                documents, metadatas, distances, strict=True
            )
        ]

    def delete(self, ordenanza_id: str) -> None:
        """Elimina todos los fragmentos de una ordenanza."""
        self._collection.delete(where={"ordenanza_id": ordenanza_id})

    def reset(self) -> None:
        """Vacía la colección (recreándola). Útil para aislar tests."""
        self._client.delete_collection(self._name)
        self._collection = self._get_or_create()


# Instancia única compartida por toda la aplicación.
_vector_store: ArticleVectorStore | None = None


def get_vector_store() -> ArticleVectorStore:
    """Dependencia de FastAPI que expone el almacén vectorial compartido."""
    global _vector_store
    if _vector_store is None:
        _vector_store = ArticleVectorStore(get_settings())
    return _vector_store
