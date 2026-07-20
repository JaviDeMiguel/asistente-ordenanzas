"""Acceso a datos de los metadatos de ordenanzas (SQLite).

Guarda los metadatos relacionales de cada ordenanza (título, fuente, recuento
de artículos y fragmentos). El texto de los artículos y sus embeddings viven en
la base de datos vectorial (ChromaDB, ver `vector_store.py`).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Depends

from app.db import Database, get_database


@dataclass
class OrdinanceRecord:
    """Ordenanza almacenada (metadatos)."""

    id: str
    titulo: str
    fuente: str | None
    articulo_count: int
    chunk_count: int
    char_count: int
    created_at: str


class OrdinanceRepository:
    """Operaciones de persistencia sobre los metadatos de ordenanzas."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        *,
        titulo: str,
        fuente: str | None,
        articulo_count: int,
        chunk_count: int,
        char_count: int,
    ) -> OrdinanceRecord:
        """Guarda los metadatos de una ordenanza y devuelve el registro creado."""
        record = OrdinanceRecord(
            id=uuid4().hex,
            titulo=titulo,
            fuente=fuente,
            articulo_count=articulo_count,
            chunk_count=chunk_count,
            char_count=char_count,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._db.execute(
            "INSERT INTO ordenanzas (id, titulo, fuente, articulo_count, "
            "chunk_count, char_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.titulo,
                record.fuente,
                record.articulo_count,
                record.chunk_count,
                record.char_count,
                record.created_at,
            ),
        )
        return record

    def list_all(self) -> list[OrdinanceRecord]:
        """Lista todas las ordenanzas indexadas (más recientes primero)."""
        rows = self._db.query_all(
            "SELECT id, titulo, fuente, articulo_count, chunk_count, char_count, "
            "created_at FROM ordenanzas ORDER BY created_at DESC"
        )
        return [self._to_record(row) for row in rows]

    def get(self, ordenanza_id: str) -> OrdinanceRecord | None:
        """Recupera una ordenanza por su id, o `None` si no existe."""
        row = self._db.query_one(
            "SELECT id, titulo, fuente, articulo_count, chunk_count, char_count, "
            "created_at FROM ordenanzas WHERE id = ?",
            (ordenanza_id,),
        )
        return self._to_record(row) if row is not None else None

    def delete(self, ordenanza_id: str) -> bool:
        """Elimina una ordenanza. Devuelve `True` si existía."""
        if self.get(ordenanza_id) is None:
            return False
        self._db.execute("DELETE FROM ordenanzas WHERE id = ?", (ordenanza_id,))
        return True

    @staticmethod
    def _to_record(row) -> OrdinanceRecord:
        return OrdinanceRecord(
            id=row["id"],
            titulo=row["titulo"],
            fuente=row["fuente"],
            articulo_count=row["articulo_count"],
            chunk_count=row["chunk_count"],
            char_count=row["char_count"],
            created_at=row["created_at"],
        )


def get_ordinance_repository(
    db: Database = Depends(get_database),
) -> OrdinanceRepository:
    """Dependencia de FastAPI que construye el repositorio de ordenanzas."""
    return OrdinanceRepository(db)
