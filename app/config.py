"""Configuración de la aplicación cargada desde variables de entorno / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ajustes globales de la aplicación.

    Los valores se leen (en este orden de prioridad) de las variables de
    entorno del proceso y del archivo `.env` de la raíz del proyecto.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Anthropic / Claude ---
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-8"
    max_answer_tokens: int = 2048

    # --- Base de datos (metadatos de las ordenanzas) ---
    db_path: str = "data/app.db"

    # --- Base de datos vectorial (ChromaDB) ---
    # Directorio de persistencia de Chroma. Usa ":memory:" para una instancia
    # efímera en memoria (útil en tests).
    chroma_path: str = "data/chroma"
    chroma_collection: str = "articulos"

    # --- Fragmentación por artículo ---
    # Un artículo se indexa entero salvo que supere `article_max_words`, en cuyo
    # caso se parte en sub-fragmentos que repiten los metadatos del artículo.
    article_max_words: int = 220
    article_overlap: int = 40

    # --- Recuperación (retrieval por embeddings) ---
    top_k: int = 5

    # Proveedor de embeddings: "local" (por defecto, sin claves ni red) o
    # "voyage" (embeddings semánticos de Voyage AI, el partner recomendado por
    # Anthropic para usar con Claude). Para preguntas legales reales conviene
    # "voyage": las consultas en lenguaje natural rara vez comparten vocabulario
    # con el texto del artículo, y el proveedor local solo mide coincidencia
    # léxica.
    embedding_provider: str = "local"
    embedding_dim: int = 512

    # --- Voyage AI (embeddings semánticos, opcional) ---
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5"
    # Dimensión de salida del vector (voyage-3.5 admite 256/512/1024/2048).
    # None => se usa la dimensión por defecto del modelo.
    voyage_embedding_dim: int | None = None


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de la configuración."""
    return Settings()
