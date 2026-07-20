"""Utilidades de texto: limpieza del Boletín, tokenización y troceado.

- `clean_boletin_text` elimina las cabeceras y pies que el Boletín Oficial de
  la Provincia (BOP) repite en cada página. Si no se limpian, esas líneas se
  intercalan en el articulado y parten los artículos por la mitad.
- `tokenize` alimenta al proveedor de embeddings local.
- `split_words` trocea un artículo demasiado largo en sub-fragmentos con
  solapamiento (cada uno conserva los metadatos del artículo).
"""

import re

# Palabras vacías (stopwords) básicas en español. No aportan a la hora de medir
# la similitud entre la pregunta y los fragmentos.
_STOPWORDS: frozenset[str] = frozenset([
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al",
    "y", "o", "u", "en", "con", "por", "para", "que", "como", "se", "su", "sus",
    "lo", "le", "les", "es", "son", "fue", "ser", "este", "esta", "estos", "estas",
    "ese", "esa", "eso", "no", "ni", "si", "más", "mas",
])

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Líneas de cabecera/pie del BOP que se repiten en cada página. Se eliminan
# ANTES de detectar la estructura del articulado.
_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*B\.?\s*O\.?\s*P\.?\s+de\s+\w+.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Bolet[íi]n\s+Oficial.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*N[úu]mero\s+\d+.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Dep[óo]sito\s+Legal.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Franqueo\s+Concertado.*$", re.IGNORECASE | re.MULTILINE),
    # Encabezado de fecha del boletín, p. ej. "25 Febrero 2013 B.O.P. de Toledo".
    re.compile(
        r"^\s*\d{1,2}\s+\w+\s+\d{4}\s+B\.?\s*O\.?\s*P\.?.*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    # Números de página sueltos en su propia línea.
    re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE),
)


def clean_boletin_text(text: str) -> str:
    """Elimina las cabeceras/pies del BOP y normaliza los espacios en blanco."""
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub("", text)
    # Colapsa 3 o más saltos de línea en dos (separación de párrafo).
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Recorta espacios finales de cada línea.
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """Divide el texto en tokens en minúsculas, ignorando stopwords y números."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def split_words(text: str, max_words: int, overlap: int) -> list[str]:
    """Trocea `text` en fragmentos de hasta `max_words` palabras con solapamiento.

    Se usa solo para artículos excepcionalmente largos. Para artículos normales
    el troceador de ordenanzas emite un único fragmento por artículo.
    """
    if max_words <= 0:
        raise ValueError("max_words debe ser mayor que 0")
    if overlap < 0 or overlap >= max_words:
        raise ValueError("overlap debe estar en el rango [0, max_words)")

    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]

    step = max_words - overlap
    chunks: list[str] = []
    for start in range(0, len(words), step):
        fragment = " ".join(words[start : start + max_words]).strip()
        if fragment:
            chunks.append(fragment)
        if start + max_words >= len(words):
            break
    return chunks
