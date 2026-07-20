"""Generación de embeddings y búsqueda por similitud.

Se define una interfaz `EmbeddingProvider` para que el proveedor de embeddings
sea intercambiable. Hay dos implementaciones:

- `LocalHashingEmbedding` (por defecto): **local y determinista**. No requiere
  claves de API, descargas de modelos ni dependencias pesadas, y funciona sin
  conexión. Proyecta cada texto a un vector de dimensión fija usando el truco
  del *hashing* (feature hashing) sobre palabras y n-gramas de caracteres.
  Es rápido y sin coste, pero **no es semántico**: mide coincidencia de
  palabras, no de significado. Suficiente para tests y demos; para consultas
  legales reales conviene el proveedor semántico.

- `VoyageEmbedding`: embeddings **semánticos** de Voyage AI (el partner de
  embeddings recomendado por Anthropic para usar con Claude). Entiende el
  significado, por lo que recupera mejor los artículos relevantes aunque la
  pregunta use palabras distintas a las del texto legal. Requiere una clave de
  API (`VOYAGE_API_KEY`) y el paquete `voyageai`.

Todos los vectores se normalizan (norma L2 = 1), de modo que la similitud de
coseno se reduce a un producto escalar.
"""

import hashlib
import math
from typing import Protocol

from app.config import Settings
from app.services.text_utils import tokenize

# Nº de textos por llamada a la API de Voyage al vectorizar en lote.
_VOYAGE_BATCH_SIZE = 128


class EmbeddingConfigurationError(RuntimeError):
    """Falta configuración (o una dependencia) para el proveedor elegido."""


class EmbeddingProvider(Protocol):
    """Contrato que debe cumplir cualquier proveedor de embeddings."""

    def embed_query(self, text: str) -> list[float]:
        """Vector (normalizado) de una consulta (la pregunta del usuario)."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Vectores (normalizados) de una lista de fragmentos."""
        ...


def _normalize(vector: list[float]) -> list[float]:
    """Devuelve el vector normalizado a norma L2 = 1 (o tal cual si es nulo)."""
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        return [v / norm for v in vector]
    return list(vector)


class LocalHashingEmbedding:
    """Embedding local mediante feature hashing de palabras y n-gramas."""

    def __init__(self, dim: int = 512) -> None:
        if dim <= 0:
            raise ValueError("dim debe ser mayor que 0")
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        """Vectoriza un único texto (consultas y documentos son equivalentes)."""
        vector = [0.0] * self._dim
        for token in tokenize(text):
            self._add_feature(vector, token, weight=1.0)
            for ngram in self._char_ngrams(token, 3):
                self._add_feature(vector, f"#{ngram}", weight=0.5)
        return _normalize(vector)

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def _add_feature(self, vector: list[float], feature: str, weight: float) -> None:
        digest = hashlib.md5(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % self._dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * weight

    @staticmethod
    def _char_ngrams(token: str, n: int) -> list[str]:
        padded = f"^{token}$"
        if len(padded) < n:
            return [padded]
        return [padded[i : i + n] for i in range(len(padded) - n + 1)]


class VoyageEmbedding:
    """Embeddings semánticos mediante la API de Voyage AI.

    El cliente se crea de forma perezosa (en la primera llamada) para no exigir
    la clave ni el paquete `voyageai` si el proveedor no se usa. Distingue entre
    documentos y consultas vía `input_type`, lo que mejora la recuperación.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "voyage-3.5",
        output_dimension: int | None = None,
    ) -> None:
        if not api_key:
            raise EmbeddingConfigurationError(
                "Falta VOYAGE_API_KEY. Configúrala en .env o como variable de "
                "entorno para usar el proveedor de embeddings 'voyage'."
            )
        self._api_key = api_key
        self._model = model
        self._output_dimension = output_dimension
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import voyageai
            except ImportError as exc:  # pragma: no cover - depende del entorno
                raise EmbeddingConfigurationError(
                    "El proveedor 'voyage' requiere el paquete 'voyageai'. "
                    "Instálalo con: pip install voyageai"
                ) from exc
            self._client = voyageai.Client(api_key=self._api_key)
        return self._client

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], input_type="query")[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(list(texts), input_type="document")

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _VOYAGE_BATCH_SIZE):
            batch = texts[start : start + _VOYAGE_BATCH_SIZE]
            kwargs = {"model": self._model, "input_type": input_type}
            if self._output_dimension is not None:
                kwargs["output_dimension"] = self._output_dimension
            result = client.embed(batch, **kwargs)
            vectors.extend(_normalize(vector) for vector in result.embeddings)
        return vectors


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Construye el proveedor de embeddings según la configuración."""
    provider = settings.embedding_provider.strip().lower()
    if provider == "local":
        return LocalHashingEmbedding(dim=settings.embedding_dim)
    if provider == "voyage":
        return VoyageEmbedding(
            api_key=settings.voyage_api_key,
            model=settings.voyage_model,
            output_dimension=settings.voyage_embedding_dim,
        )
    raise EmbeddingConfigurationError(
        f"Proveedor de embeddings desconocido: {provider!r}. Usa 'local' o 'voyage'."
    )
