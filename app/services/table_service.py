"""Linealización de tablas estructuradas a filas citables y recuperables.

Las tablas de las ordenanzas del BOP van **incrustadas como imágenes** (no hay
capa de texto con los valores), por lo que no se pueden extraer con pypdf ni
pdfplumber; y para umbrales legales una lectura automática errónea es peor que
no responder. Por eso las tablas se ingieren de forma **estructurada** (valores
verificados) y aquí se convierten en **una fila autodescriptiva por registro**,
de modo que:

  - la recuperación funcione con preguntas en lenguaje natural (la fila incluye
    la etiqueta de la fila y los nombres de columna, p. ej. «uso residencial …
    periodo noche 55 dB»), y
  - la respuesta pueda **citar** «Tabla 1 (Artículo 10)».
"""

from dataclasses import dataclass

from app.models.schemas import TableSpec

_SIN_DATO = "s/d"


@dataclass
class TableFragment:
    """Una fila de tabla linealizada, lista para indexar."""

    articulo: str
    tabla: str
    epigrafe: str  # descripción de la tabla
    texto: str     # fila autodescriptiva (clave + valores por columna)
    fila: int      # índice de la fila dentro de la tabla


def _detalle_fila(columnas: list[str], valores: list[str], unidad: str) -> str:
    """'col1: v1 dB; col2: v2 dB; …' tolerando desalineación de longitudes."""
    sufijo = f" {unidad}" if unidad else ""
    partes = []
    for i, col in enumerate(columnas):
        valor = valores[i] if i < len(valores) else _SIN_DATO
        partes.append(f"{col}: {valor}{sufijo}")
    return "; ".join(partes)


def linearize_table(spec: TableSpec) -> list[TableFragment]:
    """Convierte una tabla estructurada en una lista de filas linealizadas."""
    fragmentos: list[TableFragment] = []
    for indice, fila in enumerate(spec.filas):
        detalle = _detalle_fila(spec.columnas, fila.valores, spec.unidad)
        fragmentos.append(
            TableFragment(
                articulo=spec.articulo,
                tabla=spec.tabla,
                epigrafe=spec.descripcion,
                texto=f"{fila.clave}: {detalle}.",
                fila=indice,
            )
        )
    return fragmentos
