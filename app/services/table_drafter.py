"""Drafter de tablas por visión: transcribe tablas-imagen a borradores `TableSpec`.

Las tablas de las ordenanzas del BOP van **incrustadas como imágenes** (la capa
de texto no contiene los valores), así que ni `pypdf` ni `pdfplumber` pueden
extraerlas. Transcribirlas a mano es tedioso. Este módulo automatiza el borrador:

  1. Selecciona las páginas del PDF que llevan una tabla numerada
     (`find_table_pages`, sobre el texto de cada página).
  2. Renderiza cada página a PNG (`pypdfium2`, import perezoso).
  3. Se la pasa a **Claude (visión)** junto con el texto de la página como
     contexto, pidiéndole que transcriba las tablas a JSON estructurado
     (`output_config.format`, compatible con el pensamiento adaptativo).
  4. Valida ese JSON contra el esquema `TableSpec` (`parse_table_drafts`) y marca
     los problemas (celdas desalineadas, confianza baja, entradas inválidas).

El resultado es un **borrador para revisión humana**, no una ingesta directa:
para un umbral legal, un número mal leído es peor que no responder. El operador
verifica los valores y luego los publica con el campo `tablas` del alta.

Este módulo es una herramienta offline (no lo importa el runtime de la API); las
dependencias de render (`pypdfium2`, `Pillow`) se importan de forma perezosa y
viven en `requirements-drafter.txt`, no en `requirements.txt`.
"""

from __future__ import annotations

import base64
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.config import Settings
from app.models.schemas import TableRow, TableSpec
from app.services.llm_service import LLMConfigurationError

# Marca las celdas sin dato; coincide con `_SIN_DATO` de `table_service`.
_SIN_DATO = "s/d"

# Detecta el encabezado de una tabla numerada ("Tabla 1", "TABLA 5", "tabla nº 6").
_CAPTION_RE = re.compile(r"tabla\s+n?[.ºo\s]*\d+", re.IGNORECASE)

_SYSTEM_PROMPT = (
    "Eres un transcriptor meticuloso de tablas de ordenanzas municipales (BOP). "
    "Recibes la imagen de una página que contiene una o más tablas numeradas y, "
    "como apoyo, el texto de esa página. Transcribe EXACTAMENTE los valores tal "
    "como aparecen en la imagen: no redondees, no interpretes, no completes ni "
    "corrijas. Copia cada número tal cual. Si una celda está vacía o es "
    f"ilegible, usa '{_SIN_DATO}'. Alinea cada valor con su columna. No inventes "
    "filas ni columnas ni tablas que no estén en la imagen. Marca "
    "`confianza: 'baja'` cuando la imagen sea de baja calidad o dudes de algún "
    "valor, y explica en `nota` qué hay que revisar. Devuelve únicamente las "
    "tablas realmente presentes en la imagen."
)

_USER_INSTRUCTION = (
    "Transcribe todas las tablas numeradas de esta página. Para cada tabla "
    "indica: `tabla` (su etiqueta, p. ej. 'Tabla 1'), `articulo` (el número del "
    "artículo al que pertenece; búscalo en el texto de la página, y si no "
    "aparece deja tu mejor estimación e indícalo en `nota`), `descripcion`, "
    "`columnas` (las etiquetas de las columnas de valores), `unidad` (la unidad "
    "de los valores si la hay, p. ej. 'dB', o cadena vacía) y `filas` (cada una "
    "con `clave` y `valores` alineados con las columnas)."
)

# Esquema de salida estructurada. Sin restricciones no soportadas (min/max,
# additionalProperties != false); la validación fina la hace `TableSpec` después.
_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tablas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "tabla": {"type": "string"},
                    "articulo": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "unidad": {"type": "string"},
                    "columnas": {"type": "array", "items": {"type": "string"}},
                    "filas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "clave": {"type": "string"},
                                "valores": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["clave", "valores"],
                        },
                    },
                    "confianza": {
                        "type": "string",
                        "enum": ["alta", "media", "baja"],
                    },
                    "nota": {"type": "string"},
                },
                "required": [
                    "tabla",
                    "articulo",
                    "descripcion",
                    "unidad",
                    "columnas",
                    "filas",
                    "confianza",
                    "nota",
                ],
            },
        }
    },
    "required": ["tablas"],
}


@dataclass
class TableDraft:
    """Un borrador de tabla: la `TableSpec` más metadatos para la revisión."""

    spec: TableSpec
    pagina: int
    confianza: str = "media"
    nota: str = ""


@dataclass
class TableDraftResult:
    """Resultado del drafter: borradores + avisos + páginas analizadas."""

    borradores: list[TableDraft] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    paginas: list[int] = field(default_factory=list)

    def to_document(self) -> dict:
        """Serializa a un documento de revisión (JSON listo para editar y publicar).

        El campo `tablas` reproduce el esquema del alta de una ordenanza (campo
        `tablas`), listo para publicar una vez revisado. `borradores` añade la
        página, la confianza y la nota de cada tabla para facilitar el repaso.
        """
        return {
            "revisar_antes_de_indexar": True,
            "paginas_analizadas": self.paginas,
            "avisos": self.avisos,
            "borradores": [
                {
                    "pagina": d.pagina,
                    "confianza": d.confianza,
                    "nota": d.nota,
                    "tabla": d.spec.model_dump(),
                }
                for d in self.borradores
            ],
            "tablas": [d.spec.model_dump() for d in self.borradores],
        }


def find_table_pages(page_texts: list[str]) -> list[int]:
    """Índices (0-based) de las páginas cuyo texto contiene una tabla numerada.

    Filtra las páginas candidatas para no mandar el PDF entero al modelo de
    visión: solo las que mencionan «Tabla N» en su capa de texto.
    """
    return [i for i, texto in enumerate(page_texts) if _CAPTION_RE.search(texto or "")]


def parse_table_drafts(raw: object, *, pagina: int) -> tuple[list[TableDraft], list[str]]:
    """Valida la respuesta del modelo (una página) en borradores `TableSpec`.

    Args:
        raw: JSON devuelto por el modelo (`{"tablas": [...]}` o directamente la
            lista de tablas).
        pagina: Número de página (1-based) del que provienen, para los avisos.

    Returns:
        `(borradores, avisos)`: las tablas válidas envueltas en `TableDraft` y una
        lista de avisos legibles (entradas descartadas, celdas desalineadas,
        confianza baja) para que el humano sepa qué revisar.
    """
    if isinstance(raw, dict):
        tablas = raw.get("tablas", [])
    elif isinstance(raw, list):
        tablas = raw
    else:
        tablas = []

    borradores: list[TableDraft] = []
    avisos: list[str] = []

    for i, entry in enumerate(tablas or [], start=1):
        if not isinstance(entry, dict):
            avisos.append(f"Página {pagina}, tabla #{i}: formato inesperado; descartada.")
            continue
        try:
            spec = TableSpec(
                tabla=entry["tabla"],
                articulo=entry["articulo"],
                descripcion=entry["descripcion"],
                columnas=entry["columnas"],
                unidad=entry.get("unidad", ""),
                filas=[
                    TableRow(clave=f["clave"], valores=f["valores"])
                    for f in entry["filas"]
                ],
            )
        except (KeyError, TypeError, ValidationError) as exc:
            avisos.append(
                f"Página {pagina}, tabla #{i}: descartada por datos inválidos ({exc})."
            )
            continue

        for fila in spec.filas:
            if len(fila.valores) != len(spec.columnas):
                avisos.append(
                    f"Página {pagina}, {spec.tabla}: la fila «{fila.clave}» tiene "
                    f"{len(fila.valores)} valores pero hay {len(spec.columnas)} "
                    "columnas; revisar."
                )

        confianza = str(entry.get("confianza") or "media")
        nota = str(entry.get("nota") or "")
        if confianza == "baja":
            aviso = (
                f"Página {pagina}, {spec.tabla}: confianza BAJA del modelo — "
                "revisar con cuidado."
            )
            if nota:
                aviso = f"{aviso} Nota: {nota}"
            avisos.append(aviso)

        borradores.append(TableDraft(spec=spec, pagina=pagina, confianza=confianza, nota=nota))

    return borradores, avisos


def draft_tables(
    *,
    indices: list[int],
    render_page: Callable[[int], bytes],
    transcribe_page: Callable[[bytes, int], object],
) -> TableDraftResult:
    """Orquesta el borrado de tablas sobre un conjunto de páginas.

    Cada página se renderiza (`render_page`) y se transcribe (`transcribe_page`),
    y su JSON se valida en borradores. Las dependencias de render y de red se
    inyectan, de modo que la orquestación es comprobable sin PDF ni API real.
    """
    borradores: list[TableDraft] = []
    avisos: list[str] = []
    for idx in indices:
        png = render_page(idx)
        raw = transcribe_page(png, idx)
        pagina_borradores, pagina_avisos = parse_table_drafts(raw, pagina=idx + 1)
        borradores.extend(pagina_borradores)
        avisos.extend(pagina_avisos)
    return TableDraftResult(
        borradores=borradores,
        avisos=avisos,
        paginas=[idx + 1 for idx in indices],
    )


def _extract_page_texts(pdf_bytes: bytes) -> list[str]:  # pragma: no cover
    """Texto de cada página del PDF (para seleccionar páginas y dar contexto)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(page.extract_text() or "") for page in reader.pages]


def _render_page_png(pdf_bytes: bytes, index: int, scale: float) -> bytes:  # pragma: no cover
    """Renderiza una página del PDF a PNG con `pypdfium2` (import perezoso)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        image = pdf[index].render(scale=scale).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


class TableDrafter:
    """Genera borradores de `TableSpec` desde un PDF usando Claude (visión).

    Encapsula la interacción con el SDK de Anthropic (igual que `LLMService`) y
    los adaptadores de render, ambos inyectables para poder testear sin red.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: object | None = None,
        render_page: Callable[[bytes, int, float], bytes] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._render_page = render_page or _render_page_png

    def _get_client(self) -> object:
        """Devuelve el cliente de Anthropic (perezoso; exige `ANTHROPIC_API_KEY`)."""
        if self._client is None:
            if not self._settings.anthropic_api_key:  # pragma: no cover - trivial
                raise LLMConfigurationError(
                    "Falta ANTHROPIC_API_KEY. Configúrala en el archivo .env o "
                    "como variable de entorno para usar el drafter de tablas."
                )
            import anthropic  # pragma: no cover - camino de red

            self._client = anthropic.Anthropic(  # pragma: no cover - camino de red
                api_key=self._settings.anthropic_api_key
            )
        return self._client

    def _transcribe_page(self, png_bytes: bytes, page_text: str, index: int) -> dict:
        """Pide a Claude que transcriba las tablas de una página-imagen a JSON."""
        client = self._get_client()
        contexto = page_text.strip()
        instruccion = _USER_INSTRUCTION
        if contexto:
            instruccion = f"{instruccion}\n\nTexto de la página (contexto):\n{contexto}"
        response = client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.drafter_max_tokens,
            thinking={"type": "adaptive"},
            system=_SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": _DRAFT_SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(png_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": instruccion},
                    ],
                }
            ],
        )
        texto = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not texto:
            return {"tablas": []}
        import json

        return json.loads(texto)

    def draft_from_pdf(
        self, pdf_bytes: bytes, *, paginas: list[int] | None = None
    ) -> TableDraftResult:
        """Genera borradores de tablas para un PDF.

        Args:
            pdf_bytes: Contenido del PDF.
            paginas: Números de página (1-based) a analizar. Si es `None`, se
                detectan automáticamente las páginas con tablas numeradas.
        """
        page_texts = _extract_page_texts(pdf_bytes)
        if paginas is not None:
            indices = sorted({p - 1 for p in paginas if 1 <= p <= len(page_texts)})
        else:
            indices = find_table_pages(page_texts)
        return draft_tables(
            indices=indices,
            render_page=lambda i: self._render_page(
                pdf_bytes, i, self._settings.drafter_render_scale
            ),
            transcribe_page=lambda png, i: self._transcribe_page(png, page_texts[i], i),
        )
