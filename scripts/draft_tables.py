"""CLI: genera borradores de tablas de una ordenanza (PDF) con Claude (visión).

Las tablas del BOP van incrustadas como imágenes: este script renderiza las
páginas que llevan una tabla y las transcribe a un **borrador** `TableSpec` en
JSON. El resultado NO se indexa automáticamente: un humano verifica los valores
(un número mal leído en un umbral legal es peor que no responder) y luego los
publica con el campo `tablas` del alta de la ordenanza.

Uso (desde la raíz del proyecto, con `requirements-drafter.txt` instalado y
`ANTHROPIC_API_KEY` en el entorno o en `.env`):

    python scripts/draft_tables.py ruta/al/ordenanza.pdf
    python scripts/draft_tables.py ordenanza.pdf --paginas 3,6,7 -o tablas.json

El archivo de salida (`<pdf>.tablas.json` por defecto) trae:
  - `tablas`: la lista lista para publicar (revísala primero).
  - `borradores`: cada tabla con su página, confianza y nota.
  - `avisos`: qué revisar (celdas desalineadas, confianza baja, descartes).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.llm_service import LLMConfigurationError  # noqa: E402
from app.services.table_drafter import TableDrafter  # noqa: E402


def _parse_paginas(valor: str | None) -> list[int] | None:
    if not valor:
        return None
    return [int(p) for p in valor.replace(" ", "").split(",") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Ruta al PDF de la ordenanza.")
    parser.add_argument(
        "-o",
        "--salida",
        type=Path,
        default=None,
        help="Ruta del JSON de salida (por defecto: <pdf>.tablas.json).",
    )
    parser.add_argument(
        "--paginas",
        default=None,
        help="Páginas (1-based) a analizar, separadas por comas. Si se omite, se "
        "detectan automáticamente las que llevan una tabla numerada.",
    )
    args = parser.parse_args(argv)

    if not args.pdf.is_file():
        parser.error(f"No existe el archivo: {args.pdf}")

    salida = args.salida or args.pdf.with_suffix(".tablas.json")

    drafter = TableDrafter(get_settings())
    try:
        resultado = drafter.draft_from_pdf(
            args.pdf.read_bytes(), paginas=_parse_paginas(args.paginas)
        )
    except LLMConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    documento = resultado.to_document()
    salida.write_text(json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Páginas analizadas: {resultado.paginas or '(ninguna con tabla)'}")
    print(f"Tablas en borrador: {len(resultado.borradores)}")
    for d in resultado.borradores:
        print(f"  - {d.spec.tabla} (Art. {d.spec.articulo}, pág. {d.pagina}) "
              f"· {len(d.spec.filas)} filas · confianza {d.confianza}")
    if resultado.avisos:
        print("\nAvisos (revisar):")
        for aviso in resultado.avisos:
            print(f"  ! {aviso}")
    print(f"\n⚠️  BORRADOR sin verificar. Revisa los valores en {salida} antes de indexar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
