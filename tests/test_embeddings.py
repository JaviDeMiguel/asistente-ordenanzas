"""Tests del proveedor de embeddings (local y Voyage con cliente simulado)."""

import pytest

from app.config import Settings
from app.services.embedding_service import (
    EmbeddingConfigurationError,
    LocalHashingEmbedding,
    VoyageEmbedding,
    get_embedding_provider,
)


def test_local_embed_normaliza_y_respeta_dimension():
    emb = LocalHashingEmbedding(dim=64)
    vector = emb.embed_query("inspección técnica de edificios")
    assert len(vector) == 64
    norma = sum(v * v for v in vector) ** 0.5
    assert abs(norma - 1.0) < 1e-9


def test_local_embed_documents_devuelve_uno_por_texto():
    emb = LocalHashingEmbedding(dim=32)
    vectores = emb.embed_documents(["uno", "dos", "tres"])
    assert len(vectores) == 3


def test_local_dim_invalida():
    with pytest.raises(ValueError):
        LocalHashingEmbedding(dim=0)


def test_get_provider_local():
    provider = get_embedding_provider(Settings(embedding_provider="local"))
    assert isinstance(provider, LocalHashingEmbedding)


def test_get_provider_desconocido():
    with pytest.raises(EmbeddingConfigurationError):
        get_embedding_provider(Settings(embedding_provider="otro"))


def test_get_provider_voyage_sin_clave():
    with pytest.raises(EmbeddingConfigurationError):
        get_embedding_provider(
            Settings(embedding_provider="voyage", voyage_api_key=None)
        )


class _FakeResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeVoyageClient:
    """Cliente de Voyage simulado: devuelve vectores fijos sin tocar la red."""

    def __init__(self):
        self.kwargs = []

    def embed(self, batch, **kwargs):
        self.kwargs.append(kwargs)
        return _FakeResult([[float(i), 1.0, 0.0] for i, _ in enumerate(batch)])


def test_voyage_con_cliente_simulado():
    emb = VoyageEmbedding(api_key="clave", output_dimension=1024)
    emb._client = _FakeVoyageClient()  # inyectamos el doble

    consulta = emb.embed_query("¿cada cuánto la inspección?")
    assert len(consulta) == 3
    assert abs(sum(v * v for v in consulta) ** 0.5 - 1.0) < 1e-9

    docs = emb.embed_documents(["a", "b"])
    assert len(docs) == 2
    # Se pasa la dimensión de salida a la API.
    assert emb._client.kwargs[-1]["output_dimension"] == 1024


def test_voyage_embed_documents_vacio():
    emb = VoyageEmbedding(api_key="clave")
    assert emb.embed_documents([]) == []
