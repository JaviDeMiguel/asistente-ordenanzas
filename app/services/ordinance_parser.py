"""Troceado estructural de ordenanzas municipales en artículos.

Convierte el texto corrido de una ordenanza (extraído de un PDF del BOP o
pegado como texto plano) en una lista de artículos con su contexto jerárquico
(Título / Capítulo / Sección). Cada artículo se indexa entero, de modo que la
recuperación devuelve el artículo completo y su número —para poder citarlo— en
lugar de un fragmento arbitrario de N palabras.

Heurística clave: en las ordenanzas del BOP los artículos se numeran de forma
**secuencial** (1, 2, 3, …). Aprovechamos eso para separar los encabezados
reales de artículo de las referencias en el cuerpo del texto (p. ej. «según el
artículo 76 del R.D.T.R.L.O.T.A.U.»): solo se acepta como nuevo artículo el
encabezado cuyo número es exactamente el del anterior más uno. Así una mención
a un artículo lejano no parte el articulado.
"""

import re
from dataclasses import dataclass

from app.services.text_utils import clean_boletin_text, split_words

# Encabezado de artículo al inicio de línea. La «A» mayúscula (sin IGNORECASE)
# evita las referencias en minúscula del cuerpo («el artículo 23 de esta
# ordenanza»); admite «Artículo», «Articulo» (sin tilde) y «Artículo.» (con
# punto). El epígrafe (título del artículo) queda en el segundo grupo.
_ARTICULO_RE = re.compile(
    r"^[ \t]*Art[íi]culo\.?\s+(\d+)\s*[.\-–)º]*\s*(.*?)\s*$",
    re.MULTILINE,
)
_TITULO_RE = re.compile(
    r"^[ \t]*T[ÍI]TULO\s+([IVXLCDM]+|\d+)\b[.\-–:]*\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CAPITULO_RE = re.compile(
    r"^[ \t]*CAP[ÍI]TULO\s+([IVXLCDM]+|\d+)[ºª]?\b[.\-–:]*\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SECCION_RE = re.compile(
    r"^[ \t]*Secci[óo]n\s+([\wÁÉÍÓÚáéíóúñÑ]+)\b[.\-–:]*\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class ParsedArticle:
    """Un artículo (o sub-fragmento de un artículo largo) con su contexto."""

    numero: str
    epigrafe: str
    titulo: str
    capitulo: str
    seccion: str
    texto: str
    parte: int = 0
    total_partes: int = 1


def _label(prefix: str, numero: str, epigrafe: str) -> str:
    """Construye una etiqueta legible, p. ej. 'Título II — La inspección…'."""
    numero = numero.strip()
    epigrafe = re.sub(r"\s+", " ", epigrafe).strip(" .:-–")
    label = f"{prefix} {numero}".strip()
    return f"{label} — {epigrafe}" if epigrafe else label


def _real_article_matches(matches: list[re.Match[str]]) -> list[re.Match[str]]:
    """Filtra los encabezados reales de artículo (numeración secuencial).

    Conserva el primero y, a partir de ahí, solo los encabezados cuyo número es
    el del último aceptado más uno. Descarta así las referencias a artículos que
    aparecen en el cuerpo del texto.
    """
    kept: list[re.Match[str]] = []
    expected: int | None = None
    for match in matches:
        numero = int(match.group(1))
        if expected is None or numero == expected:
            kept.append(match)
            expected = numero + 1
    return kept


def parse_ordinance(
    text: str, *, max_words: int, overlap: int
) -> list[ParsedArticle]:
    """Trocea una ordenanza en artículos con su contexto jerárquico.

    Args:
        text: Texto completo de la ordenanza.
        max_words: Tamaño máximo (palabras) de un fragmento; los artículos que
            lo superen se parten en sub-fragmentos con solapamiento.
        overlap: Palabras de solapamiento entre sub-fragmentos.

    Returns:
        Lista de `ParsedArticle`. Vacía si no se detecta ningún artículo (el
        llamante puede entonces recurrir a un troceado por palabras).
    """
    clean = clean_boletin_text(text)

    article_matches = _real_article_matches(list(_ARTICULO_RE.finditer(clean)))
    if not article_matches:
        return []

    # Marcadores de contexto (Título/Capítulo/Sección) con su posición y etiqueta.
    context_markers: list[tuple[int, str, str]] = []
    for regex, prefix, kind in (
        (_TITULO_RE, "Título", "titulo"),
        (_CAPITULO_RE, "Capítulo", "capitulo"),
        (_SECCION_RE, "Sección", "seccion"),
    ):
        for match in regex.finditer(clean):
            context_markers.append(
                (match.start(), kind, _label(prefix, match.group(1), match.group(2)))
            )

    # Lista ordenada de todas las fronteras: artículos (kind="art") + contexto.
    boundaries: list[tuple[int, str, str]] = [
        (match.start(), "art", "") for match in article_matches
    ]
    boundaries.extend(context_markers)
    boundaries.sort(key=lambda item: item[0])

    # Fin del texto tras el último artículo: hasta la primera "disposición" o el
    # final del documento (lo que llegue antes), para no arrastrar anexos.
    end_of_articulado = _articulado_end(clean, article_matches[-1].end())

    article_body_end = {
        match.start(): _next_boundary(boundaries, match.start(), end_of_articulado)
        for match in article_matches
    }

    articles: list[ParsedArticle] = []
    titulo = capitulo = seccion = ""
    art_index = 0
    for pos, kind, label in boundaries:
        if kind == "titulo":
            titulo, capitulo, seccion = label, "", ""
            continue
        if kind == "capitulo":
            capitulo, seccion = label, ""
            continue
        if kind == "seccion":
            seccion = label
            continue

        match = article_matches[art_index]
        art_index += 1
        numero = match.group(1)
        epigrafe = re.sub(r"\s+", " ", match.group(2)).strip(" .:-–")
        body = clean[match.end() : article_body_end[pos]].strip()

        articles.extend(
            _to_fragments(
                numero=numero,
                epigrafe=epigrafe,
                titulo=titulo,
                capitulo=capitulo,
                seccion=seccion,
                body=body,
                max_words=max_words,
                overlap=overlap,
            )
        )
    return articles


def _next_boundary(
    boundaries: list[tuple[int, str, str]], pos: int, default_end: int
) -> int:
    """Posición de la siguiente frontera estrictamente posterior a `pos`."""
    for start, _kind, _label in boundaries:
        if start > pos:
            return start
    return default_end


def _articulado_end(clean: str, last_article_end: int) -> int:
    """Corta el articulado antes de las disposiciones/anexos finales, si existen."""
    tail = re.search(
        r"^[ \t]*(Disposici[óo]n(?:es)?\b|ANEXO\b|DISPOSICI[ÓO]N)",
        clean[last_article_end:],
        re.IGNORECASE | re.MULTILINE,
    )
    return last_article_end + tail.start() if tail else len(clean)


def _to_fragments(
    *,
    numero: str,
    epigrafe: str,
    titulo: str,
    capitulo: str,
    seccion: str,
    body: str,
    max_words: int,
    overlap: int,
) -> list[ParsedArticle]:
    """Convierte un artículo en uno o varios `ParsedArticle` (si es muy largo)."""
    if not body:
        body = epigrafe or f"Artículo {numero}"
    partes = split_words(body, max_words=max_words, overlap=overlap) or [body]
    total = len(partes)
    return [
        ParsedArticle(
            numero=numero,
            epigrafe=epigrafe,
            titulo=titulo,
            capitulo=capitulo,
            seccion=seccion,
            texto=parte,
            parte=indice,
            total_partes=total,
        )
        for indice, parte in enumerate(partes)
    ]
