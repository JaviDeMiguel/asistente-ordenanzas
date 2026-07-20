"""Fixtures compartidas de pytest.

Preparan la aplicación para los tests **sin llamar a la API real del LLM**:
  - La base de datos se fuerza a SQLite en memoria (rápida y aislada).
  - La base vectorial de Chroma se fuerza a una instancia efímera en memoria.
  - `LLMService` se sustituye por un doble (`RecordingLLM`) que registra las
    llamadas y devuelve una respuesta fija, de modo que ningún test gasta cuota.
  - Los embeddings usan el proveedor local (determinista, sin red).
"""

import os

# Debe fijarse ANTES de importar la configuración de la app.
os.environ["DB_PATH"] = ":memory:"
os.environ["CHROMA_PATH"] = ":memory:"
os.environ["EMBEDDING_PROVIDER"] = "local"
# Nos aseguramos de que los tests no dependan de claves del entorno del
# desarrollador (el camino 503 espera que NO haya clave de Anthropic).
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("VOYAGE_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
import app.repositories.vector_store as vector_store_module
from app.config import get_settings
from app.main import app

# La configuración se cachea; la limpiamos para que lea las variables de test.
get_settings.cache_clear()


class RecordingLLM:
    """Doble de `LLMService`: registra las llamadas y no toca la red."""

    calls: list[dict] = []

    def __init__(self, settings=None) -> None:  # firma compatible con LLMService
        pass

    def ensure_configured(self) -> None:
        pass

    def answer(self, pregunta: str, contexto: list[str]) -> str:
        RecordingLLM.calls.append({"pregunta": pregunta, "contexto": list(contexto)})
        return "RESPUESTA-SIMULADA-DEL-LLM"


@pytest.fixture
def llm() -> type[RecordingLLM]:
    """Reinicia el registro de llamadas y expone el doble del LLM."""
    RecordingLLM.calls = []
    return RecordingLLM


@pytest.fixture
def client(monkeypatch, llm) -> TestClient:
    """Cliente de pruebas con BD y base vectorial en memoria y LLM mockeado."""
    db_module._database = None
    vector_store_module._vector_store = None
    vector_store_module.get_vector_store().reset()
    monkeypatch.setattr("app.services.qa_service.LLMService", llm)

    test_client = TestClient(app)
    yield test_client

    db_module._database = None
    vector_store_module._vector_store = None
