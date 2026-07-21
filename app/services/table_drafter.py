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

# Detecta el encabezado de una tabla o cuadro numerado, con número arábigo o
# romano: "Tabla 1", "TABLA 5", "tabla nº 6", "Cuadro 2", "Tabla V", "Cuadro II".
# El `\b` final evita falsos positivos como "Tabla de contenidos" (la 'd' de "de"
# es número romano, pero no queda en frontera de palabra).
_CAPTION_RE = re.compile(
    r"(?:tabla|cuadro)\s+(?:n[.ºo°\s]*)?(?:\d+|[ivxlcdm]+)\b", re.IGNORECASE
)

# Umbrales para tratar una imagen incrustada como tabla (y no como logo/firma):
# ancho mínimo relativo al de la página y alto mínimo en puntos PDF.
_MIN_TABLE_RATIO = 0.30
_MIN_TABLE_HEIGHT_PT = 24.0
# Solo se fusionan bboxes de imagen que casi se tocan (fragmentos de una misma
# tabla). Un valor pequeño evita mezclar tablas contiguas en maquetas a 2 columnas
# (el «canalón» entre columnas suele ser mayor que este umbral).
_MERGE_GAP_PT = 6.0

_SYSTEM_PROMPT = (
    "Eres un transcriptor meticuloso de tablas de ordenanzas municipales (BOP). "
    "Recibes la imagen de una página que contiene una o más tablas numeradas y, "
    "como apoyo, el texto de esa página. Transcribe EXACTAMENTE los valores tal "
    "como aparecen en la imagen: no redondees, no interpretes, no completes ni "
    "corrijas. Copia cada número tal cual. Si una celda está vacía o es "
    f"ilegible, usa '{_SIN_DATO}'. No inventes filas ni columnas ni tablas que no "
    "estén en la imagen. "
    # Reglas de estructura (para que la transcripción sea reproducible):
    "La etiqueta de cada fila (la primera columna, la que nombra la fila) va "
    "SIEMPRE en `clave`, NUNCA en `valores`. `columnas` contiene solo las "
    "columnas de datos (no incluyas la columna de etiquetas de fila). `valores` "
    "contiene solo las celdas de datos, una por cada columna de `columnas` y en "
    "su mismo orden. Escribe los nombres de columna tal cual, sin añadir "
    "subíndices ni prefijos (por ejemplo 'Ld', no 'L_d'). Da las filas en el "
    "orden en que aparecen en la imagen. "
    "Marca `confianza: 'baja'` cuando la imagen sea de baja calidad o dudes de "
    "algún valor, y explica en `nota` qué hay que revisar. Devuelve únicamente "
    "las tablas realmente presentes en la imagen."
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


def _slug(texto: str) -> str:
    """Convierte una etiqueta en un nombre de archivo seguro ('Tabla 3' -> 'Tabla-3')."""
    limpio = re.sub(r"[^\w.-]+", "-", texto.strip()).strip("-")
    return limpio or "tabla"


@dataclass
class TableDraft:
    """Un borrador de tabla: la `TableSpec`, metadatos y su recorte-imagen."""

    spec: TableSpec
    pagina: int
    confianza: str = "media"
    nota: str = ""
    # PNG del recorte que produjo el borrador (para revisarlo junto al JSON).
    imagen: bytes | None = None


@dataclass
class TableDraftResult:
    """Resultado del drafter: borradores + avisos + páginas analizadas."""

    borradores: list[TableDraft] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    paginas: list[int] = field(default_factory=list)

    def _named_crops(self) -> list[tuple[TableDraft, str]]:
        """Empareja cada borrador con un nombre de archivo de recorte único."""
        contador: dict[int, int] = {}
        named: list[tuple[TableDraft, str]] = []
        for d in self.borradores:
            contador[d.pagina] = contador.get(d.pagina, 0) + 1
            nombre = f"p{d.pagina}_{contador[d.pagina]:02d}_{_slug(d.spec.tabla)}.png"
            named.append((d, nombre))
        return named

    def crops(self) -> list[tuple[str, bytes]]:
        """Pares `(nombre_archivo, PNG)` de los recortes de cada tabla."""
        return [(n, d.imagen) for d, n in self._named_crops() if d.imagen is not None]

    def to_document(self, *, imagenes_dir: str = "") -> dict:
        """Serializa a un documento de revisión (JSON listo para editar y publicar).

        El campo `tablas` reproduce el esquema del alta de una ordenanza (campo
        `tablas`), listo para publicar una vez revisado. `borradores` añade la
        página, la confianza, la nota y —si `imagenes_dir` se indica— la ruta al
        recorte de cada tabla, para revisar transcripción e imagen de un vistazo.
        """
        borradores = []
        for d, nombre in self._named_crops():
            entrada = {
                "pagina": d.pagina,
                "confianza": d.confianza,
                "nota": d.nota,
                "tabla": d.spec.model_dump(),
            }
            if imagenes_dir and d.imagen is not None:
                entrada["imagen"] = f"{imagenes_dir}/{nombre}"
            borradores.append(entrada)
        return {
            "revisar_antes_de_indexar": True,
            "paginas_analizadas": self.paginas,
            "avisos": self.avisos,
            "borradores": borradores,
            "tablas": [d.spec.model_dump() for d in self.borradores],
        }


def find_table_pages(page_texts: list[str]) -> list[int]:
    """Índices (0-based) de las páginas cuyo texto contiene una tabla numerada.

    Filtra las páginas candidatas para no mandar el PDF entero al modelo de
    visión: solo las que mencionan «Tabla N» en su capa de texto.
    """
    return [i for i, texto in enumerate(page_texts) if _CAPTION_RE.search(texto or "")]


def looks_like_table(width: float, height: float, page_width: float) -> bool:
    """¿Una imagen incrustada tiene tamaño de tabla (no de logo/firma)?

    Dimensiones en puntos PDF. Descarta banners e iconos: exige un ancho
    relativo mínimo y un alto mínimo.
    """
    return width >= _MIN_TABLE_RATIO * page_width and height >= _MIN_TABLE_HEIGHT_PT


def merge_boxes(
    boxes: list[tuple[float, float, float, float]], *, gap: float = _MERGE_GAP_PT
) -> list[tuple[float, float, float, float]]:
    """Fusiona bboxes `(l, b, r, t)` que se solapan o casi se tocan (< `gap`).

    Sirve para reunir los fragmentos-imagen de una misma tabla sin mezclar tablas
    distintas (con `gap` pequeño, las columnas contiguas quedan separadas).
    """
    restantes = [tuple(b) for b in boxes]
    fusionadas: list[tuple[float, float, float, float]] = []
    while restantes:
        actual = restantes.pop()
        cambiado = True
        while cambiado:
            cambiado = False
            for i, otra in enumerate(fusionadas):
                if _boxes_close(actual, otra, gap):
                    actual = (
                        min(actual[0], otra[0]),
                        min(actual[1], otra[1]),
                        max(actual[2], otra[2]),
                        max(actual[3], otra[3]),
                    )
                    fusionadas.pop(i)
                    cambiado = True
                    break
        fusionadas.append(actual)
    return fusionadas


def _boxes_close(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    gap: float,
) -> bool:
    """¿Dos bboxes `(l, b, r, t)` se solapan o quedan a menos de `gap`?"""
    separadas = (
        a[2] + gap < b[0]
        or b[2] + gap < a[0]
        or a[3] + gap < b[1]
        or b[3] + gap < a[1]
    )
    return not separadas


def expand_selection(
    caption_pages: list[int], image_pages: list[int], *, radius: int = 1
) -> list[int]:
    """Une las páginas con leyenda de tabla y las páginas-imagen cercanas a ellas.

    Una tabla puede ir incrustada como imagen en una página cuya capa de texto no
    dice «Tabla N» (la leyenda está en la página anterior). Se añaden esas
    páginas-imagen solo si están a `radius` páginas de una con leyenda, para no
    colar portadas ni anexos con imágenes decorativas.
    """
    seleccion = set(caption_pages)
    for p in image_pages:
        if any(abs(p - c) <= radius for c in caption_pages):
            seleccion.add(p)
    return sorted(seleccion)


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


def _norm_val(valor: str) -> str:
    """Normaliza una celda para comparar valores (ignora espacios y mayúsculas)."""
    return re.sub(r"\s+", "", valor).lower()


def _compare_specs(a: TableSpec, b: TableSpec) -> tuple[list[str], list[str]]:
    """Compara dos transcripciones de la misma tabla, fila a fila por posición.

    Separa lo que importa de lo que no:
      - `conflictos_valor`: celdas de datos que no coinciden en la misma posición
        (p. ej. «fila 3 col 2: '63' vs '68'»). Para un umbral legal, esto es lo
        que hay que revisar.
      - `notas_forma`: diferencias de forma (nº de columnas o de filas, longitud
        de una fila). Son de maquetado, no de contenido, y no bastan para dudar
        de los números comparables.
    Las diferencias de *nombre* de columna o de etiqueta de fila se ignoran (no
    cambian los valores).
    """
    conflictos_valor: list[str] = []
    notas_forma: list[str] = []
    if len(a.columnas) != len(b.columnas):
        notas_forma.append(f"columnas {len(a.columnas)} vs {len(b.columnas)}")
    if len(a.filas) != len(b.filas):
        notas_forma.append(f"filas {len(a.filas)} vs {len(b.filas)}")
    for i in range(min(len(a.filas), len(b.filas))):
        fa, fb = a.filas[i], b.filas[i]
        if len(fa.valores) != len(fb.valores):
            notas_forma.append(f"fila {i + 1}: {len(fa.valores)} vs {len(fb.valores)} valores")
        for j in range(min(len(fa.valores), len(fb.valores))):
            if _norm_val(fa.valores[j]) != _norm_val(fb.valores[j]):
                conflictos_valor.append(
                    f"fila {i + 1} («{fa.clave[:24]}») col {j + 1}: "
                    f"'{fa.valores[j]}' vs '{fb.valores[j]}'"
                )
    return conflictos_valor, notas_forma


def reconcile_drafts(
    pasadas: list[list[TableDraft]], *, pagina: int
) -> tuple[list[TableDraft], list[str]]:
    """Cruza varias transcripciones de la misma región y marca discrepancias.

    Toma los borradores de cada pasada de visión (misma imagen, transcrita N
    veces) y los empareja **por posición** (cada región suele tener una tabla).
    Solo un desacuerdo de **valor** baja la confianza a «baja»; las diferencias de
    estructura o de nombres se anotan como aviso menor sin dudar de los números.
    """
    if not pasadas:
        return [], []
    base = pasadas[0]
    reconciliados: list[TableDraft] = []
    avisos: list[str] = []

    if any(len(p) != len(base) for p in pasadas[1:]):
        avisos.append(
            f"Página {pagina}: el nº de tablas detectadas difiere entre pasadas "
            f"{[len(p) for p in pasadas]}; revisar."
        )

    for i, d in enumerate(base):
        conflictos_valor: list[str] = []
        notas_forma: list[str] = []
        for otra in pasadas[1:]:
            if i < len(otra):
                cv, nf = _compare_specs(d.spec, otra[i].spec)
                conflictos_valor.extend(cv)
                notas_forma.extend(nf)
        etiqueta = d.spec.tabla
        if conflictos_valor:
            nota = f"{d.nota} [discrepancia de valores entre pasadas]".strip()
            reconciliados.append(
                TableDraft(spec=d.spec, pagina=d.pagina, confianza="baja", nota=nota)
            )
            avisos.append(
                f"Página {pagina}, {etiqueta}: discrepancia de valores entre "
                "pasadas — " + "; ".join(conflictos_valor[:6]) + " — verificar."
            )
        else:
            reconciliados.append(d)
            if notas_forma:
                unicas = list(dict.fromkeys(notas_forma))[:4]
                avisos.append(
                    f"Página {pagina}, {etiqueta}: la estructura difiere entre "
                    f"pasadas ({'; '.join(unicas)}), pero los valores comparables "
                    "coinciden."
                )
    return reconciliados, avisos


def draft_tables(
    *,
    indices: list[int],
    render_regions: Callable[[int], list[bytes]],
    transcribe_page: Callable[[bytes, int], object],
    passes: int = 1,
) -> TableDraftResult:
    """Orquesta el borrado de tablas sobre un conjunto de páginas.

    De cada página se obtienen una o varias **regiones-imagen** (`render_regions`,
    un recorte por tabla) y cada región se transcribe (`transcribe_page`). Con
    `passes > 1` cada región se transcribe varias veces y se reconcilian las
    lecturas (verificación cruzada). Las dependencias de render y de red se
    inyectan, de modo que la orquestación es comprobable sin PDF ni API real.
    """
    borradores: list[TableDraft] = []
    avisos: list[str] = []
    for idx in indices:
        for region_png in render_regions(idx):
            resultados = [
                parse_table_drafts(transcribe_page(region_png, idx), pagina=idx + 1)
                for _ in range(passes)
            ]
            if passes > 1:
                region_borradores, region_avisos = reconcile_drafts(
                    [r[0] for r in resultados], pagina=idx + 1
                )
                region_avisos = resultados[0][1] + region_avisos
            else:
                region_borradores, region_avisos = resultados[0]
            for d in region_borradores:
                d.imagen = region_png  # el recorte que produjo el borrador
            borradores.extend(region_borradores)
            avisos.extend(region_avisos)
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


def _image_table_pages(pdf_bytes: bytes) -> list[int]:  # pragma: no cover
    """Índices (0-based) de páginas con al menos una imagen con pinta de tabla."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        paginas: list[int] = []
        for i in range(len(pdf)):
            page = pdf[i]
            page_width, _ = page.get_size()
            for obj in page.get_objects():
                if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                    left, bottom, right, top = obj.get_bounds()
                    if looks_like_table(right - left, top - bottom, page_width):
                        paginas.append(i)
                        break
        return paginas
    finally:
        pdf.close()


def _render_regions(
    pdf_bytes: bytes,
    index: int,
    *,
    scale: float,
    margin_side: float,
    margin_top: float,
) -> list[bytes]:  # pragma: no cover
    """Recorta cada región-tabla de una página a PNG (`pypdfium2` + `Pillow`).

    Detecta las imágenes con pinta de tabla, fusiona las que son fragmentos de una
    misma tabla y devuelve un recorte por tabla (con margen, más amplio arriba para
    la leyenda). Si la página no tiene imágenes-tabla, devuelve la página entera.
    """
    import math

    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        page = pdf[index]
        page_width, page_height = page.get_size()
        image = page.render(scale=scale).to_pil()
        scale_x, scale_y = image.width / page_width, image.height / page_height

        boxes: list[tuple[float, float, float, float]] = []
        for obj in page.get_objects():
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                left, bottom, right, top = obj.get_bounds()
                if looks_like_table(right - left, top - bottom, page_width):
                    boxes.append((left, bottom, right, top))

        if not boxes:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return [buffer.getvalue()]

        boxes = merge_boxes(boxes)
        boxes.sort(key=lambda bx: (-bx[3], bx[0]))  # orden de lectura aproximado

        crops: list[bytes] = []
        for left, bottom, right, top in boxes:
            l2 = max(0.0, left - margin_side)
            b2 = max(0.0, bottom - margin_side)
            r2 = min(page_width, right + margin_side)
            t2 = min(page_height, top + margin_top)
            recorte = image.crop(
                (
                    int(l2 * scale_x),
                    int((page_height - t2) * scale_y),
                    math.ceil(r2 * scale_x),
                    math.ceil((page_height - b2) * scale_y),
                )
            )
            buffer = io.BytesIO()
            recorte.save(buffer, format="PNG")
            crops.append(buffer.getvalue())
        return crops
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
        render_regions: Callable[[bytes, int], list[bytes]] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._render_regions = render_regions

    def _regions(self, pdf_bytes: bytes, index: int) -> list[bytes]:
        """Recortes-imagen de una página (adaptador inyectable en tests)."""
        if self._render_regions is not None:
            return self._render_regions(pdf_bytes, index)
        return _render_regions(
            pdf_bytes,
            index,
            scale=self._settings.drafter_render_scale,
            margin_side=self._settings.drafter_crop_margin_pt,
            margin_top=self._settings.drafter_crop_margin_top_pt,
        )

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
        self,
        pdf_bytes: bytes,
        *,
        paginas: list[int] | None = None,
        verificar: bool = False,
    ) -> TableDraftResult:
        """Genera borradores de tablas para un PDF.

        Args:
            pdf_bytes: Contenido del PDF.
            paginas: Números de página (1-based) a analizar. Si es `None`, se
                detectan automáticamente (páginas con leyenda «Tabla N» más las
                páginas-imagen contiguas a ellas).
            verificar: Si es `True`, transcribe cada región dos veces y reconcilia
                las lecturas (verificación cruzada); dobla el coste en llamadas.
        """
        page_texts = _extract_page_texts(pdf_bytes)
        if paginas is not None:
            indices = sorted({p - 1 for p in paginas if 1 <= p <= len(page_texts)})
        else:
            caption_pages = find_table_pages(page_texts)
            image_pages = _image_table_pages(pdf_bytes)
            if caption_pages:
                indices = expand_selection(
                    caption_pages, image_pages, radius=self._settings.drafter_page_radius
                )
            else:
                # Sin leyendas «Tabla N» reconocibles (p. ej. PDF escaneado o con
                # otra maqueta): analiza las páginas con imagen-tabla, si las hay.
                indices = image_pages
        return draft_tables(
            indices=indices,
            render_regions=lambda i: self._regions(pdf_bytes, i),
            transcribe_page=lambda png, i: self._transcribe_page(png, page_texts[i], i),
            passes=2 if verificar else 1,
        )
