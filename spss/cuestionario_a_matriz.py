#!/usr/bin/env python3
"""De la imagen de un cuestionario a una matriz de datos para SPSS.

Flujo en dos pasos, con revisión humana entre ambos (mismo enfoque que el
drafter de tablas del repositorio: la visión propone, el humano verifica):

1. `codebook`: a partir de la imagen del cuestionario (en blanco o cumplimentado)
   Claude (visión) redacta el **libro de códigos**: una variable por pregunta,
   con su tipo y sus opciones codificadas. Se guarda como JSON para que lo
   revises y edites (nombres de variable, códigos, opciones mal leídas).

2. `extraer`: con el codebook revisado y las fotos/escaneos de los cuestionarios
   cumplimentados, extrae las respuestas de cada uno y construye la matriz:
   una fila por cuestionario, una columna por variable (las de respuesta
   múltiple se expanden a una columna 0/1 por opción). Escribe:

     - `<salida>.sav`  matriz para SPSS con etiquetas de variable y de valor
     - `<salida>.csv`  la misma matriz en texto plano
     - `<salida>.incidencias.txt`  qué revisar a mano (ilegibles, dobles
       marcas, confianza baja, discrepancias entre pasadas con --verificar)

Uso:

    export ANTHROPIC_API_KEY=...   # (en Windows: set ANTHROPIC_API_KEY=...)
    python cuestionario_a_matriz.py codebook cuestionario.png
    # revisa codebook.json y después:
    python cuestionario_a_matriz.py extraer codebook.json fotos/*.jpg --salida datos
    # cuestionarios de varias páginas (2 imágenes consecutivas por persona):
    python cuestionario_a_matriz.py extraer codebook.json fotos/*.jpg --paginas-por-caso 2

Las respuestas dudosas nunca se corrigen en silencio: quedan como valor perdido
o marcadas en el archivo de incidencias, porque en una matriz de datos una
lectura inventada es peor que un hueco.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

MODELO_POR_DEFECTO = "claude-opus-4-8"
MAX_TOKENS = 8192

TIPOS_VALIDOS = {"unica", "multiple", "numerica", "abierta"}

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_NUMERO_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

# --- Prompts y esquemas de salida estructurada ------------------------------

_CODEBOOK_SYSTEM = (
    "Eres un técnico de codificación de encuestas. Recibes las imágenes de un "
    "cuestionario (en blanco o cumplimentado) y produces su libro de códigos "
    "para una matriz de datos tipo SPSS: una variable por pregunta, en el orden "
    "del cuestionario. Nombres cortos en minúsculas (p1, p2, … o snake_case). "
    "Tipos: 'unica' (se marca una sola opción), 'multiple' (pueden marcarse "
    "varias), 'numerica' (se escribe un número: edad, escala abierta…), "
    "'abierta' (texto libre). Para 'unica' y 'multiple' enumera TODAS las "
    "opciones impresas, en su orden, con código numérico consecutivo desde 1 "
    "— salvo que el cuestionario imprima sus propios códigos, que entonces se "
    "respetan. No inventes preguntas ni opciones; si algo es ilegible o dudoso, "
    "dilo en `nota`."
)

_CODEBOOK_INSTRUCTION = (
    "Extrae el libro de códigos de este cuestionario. Devuelve una variable por "
    "pregunta, con `nombre`, `pregunta` (el enunciado literal), `tipo`, "
    "`opciones` (lista de {codigo, etiqueta}; vacía para numéricas y abiertas) "
    "y `nota` (vacía si no hay nada que avisar)."
)

_CODEBOOK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "variables": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "nombre": {"type": "string"},
                    "pregunta": {"type": "string"},
                    "tipo": {"type": "string", "enum": sorted(TIPOS_VALIDOS)},
                    "opciones": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "codigo": {"type": "string"},
                                "etiqueta": {"type": "string"},
                            },
                            "required": ["codigo", "etiqueta"],
                        },
                    },
                    "nota": {"type": "string"},
                },
                "required": ["nombre", "pregunta", "tipo", "opciones", "nota"],
            },
        }
    },
    "required": ["variables"],
}

_EXTRACT_SYSTEM = (
    "Eres un grabador de datos meticuloso. Recibes el libro de códigos de un "
    "cuestionario y las imágenes de UN cuestionario cumplimentado por una "
    "persona. Para cada variable devuelve en `valores` los códigos de las "
    "opciones marcadas: en las de respuesta única un solo código; en las "
    "múltiples todos los marcados; en las numéricas el número escrito, tal "
    "cual; en las abiertas el texto literal. Si la pregunta está sin contestar "
    "devuelve la lista vacía: NO adivines ni completes. Si una marca es dudosa "
    "(tachones, dos marcas en una pregunta de respuesta única, caligrafía "
    "difícil), pon `confianza: 'baja'` y describe en `nota` lo que ves. "
    "Transcribe lo que está marcado, no lo que sería razonable responder."
)

_EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "respuestas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "variable": {"type": "string"},
                    "valores": {"type": "array", "items": {"type": "string"}},
                    "confianza": {
                        "type": "string",
                        "enum": ["alta", "media", "baja"],
                    },
                    "nota": {"type": "string"},
                },
                "required": ["variable", "valores", "confianza", "nota"],
            },
        }
    },
    "required": ["respuestas"],
}


# --- Modelo de datos --------------------------------------------------------


@dataclass
class Variable:
    """Una variable del libro de códigos."""

    nombre: str
    pregunta: str
    tipo: str
    # código -> etiqueta, en el orden del cuestionario (solo unica/multiple).
    opciones: dict[str, str] = field(default_factory=dict)

    def codigos_numericos(self) -> bool:
        """¿Todos los códigos de las opciones son numéricos? (columna numérica)."""
        return bool(self.opciones) and all(
            _NUMERO_RE.fullmatch(c) for c in self.opciones
        )


def validar_codebook(raw: object) -> tuple[list[Variable], list[str]]:
    """Valida el codebook (del modelo o del JSON en disco) a `Variable`s.

    Acepta `{"variables": [...]}` o directamente la lista. Devuelve además los
    avisos (tipos desconocidos, nombres duplicados, preguntas cerradas sin
    opciones) para que el humano sepa qué corregir antes de extraer.
    """
    if isinstance(raw, dict):
        entradas = raw.get("variables", [])
    elif isinstance(raw, list):
        entradas = raw
    else:
        entradas = []

    variables: list[Variable] = []
    avisos: list[str] = []
    vistos: set[str] = set()

    for i, e in enumerate(entradas or [], start=1):
        if not isinstance(e, dict):
            avisos.append(f"Variable #{i}: formato inesperado; descartada.")
            continue
        nombre = str(e.get("nombre") or "").strip() or f"v{i}"
        if nombre.lower() in vistos:
            original = nombre
            n = 2
            while f"{original}_{n}".lower() in vistos:
                n += 1
            nombre = f"{original}_{n}"
            avisos.append(f"Nombre duplicado «{original}»; renombrado a «{nombre}».")
        vistos.add(nombre.lower())

        tipo = str(e.get("tipo") or "").strip().lower()
        if tipo not in TIPOS_VALIDOS:
            avisos.append(f"«{nombre}»: tipo desconocido «{tipo}»; se trata como abierta.")
            tipo = "abierta"

        opciones: dict[str, str] = {}
        for op in e.get("opciones") or []:
            if isinstance(op, dict) and str(op.get("codigo", "")).strip():
                opciones[str(op["codigo"]).strip()] = str(op.get("etiqueta", "")).strip()

        if tipo in {"unica", "multiple"} and not opciones:
            avisos.append(f"«{nombre}»: pregunta cerrada sin opciones; se trata como abierta.")
            tipo = "abierta"
        if tipo in {"numerica", "abierta"}:
            opciones = {}

        nota = str(e.get("nota") or "").strip()
        if nota:
            avisos.append(f"«{nombre}»: nota del modelo — {nota}")

        variables.append(
            Variable(
                nombre=nombre,
                pregunta=str(e.get("pregunta") or "").strip(),
                tipo=tipo,
                opciones=opciones,
            )
        )

    if not variables:
        avisos.append("El codebook no contiene ninguna variable válida.")
    return variables, avisos


def codebook_a_documento(
    variables: list[Variable], *, origen: list[str], avisos: list[str]
) -> dict:
    """Serializa el codebook a un documento JSON revisable/editable."""
    return {
        "revisar_antes_de_extraer": True,
        "origen": origen,
        "avisos": avisos,
        "variables": [
            {
                "nombre": v.nombre,
                "pregunta": v.pregunta,
                "tipo": v.tipo,
                "opciones": [
                    {"codigo": c, "etiqueta": e} for c, e in v.opciones.items()
                ],
            }
            for v in variables
        ],
    }


# --- Lectura de las respuestas de un caso -----------------------------------


def parse_respuestas(
    raw: object, variables: list[Variable], *, caso: str
) -> tuple[dict[str, list[str]], list[str]]:
    """Valida la respuesta del modelo para un cuestionario cumplimentado.

    Devuelve `(respuestas, incidencias)`: `respuestas` mapea nombre de variable a
    la lista de valores leídos (vacía = sin contestar), e `incidencias` recoge lo
    que un humano debe revisar (confianza baja, variables sin lectura, variables
    desconocidas devueltas por el modelo).
    """
    if isinstance(raw, dict):
        entradas = raw.get("respuestas", [])
    elif isinstance(raw, list):
        entradas = raw
    else:
        entradas = []

    conocidas = {v.nombre for v in variables}
    respuestas: dict[str, list[str]] = {}
    incidencias: list[str] = []

    for e in entradas or []:
        if not isinstance(e, dict) or not str(e.get("variable", "")).strip():
            continue
        nombre = str(e["variable"]).strip()
        if nombre not in conocidas:
            incidencias.append(
                f"{caso}: el modelo devolvió una variable desconocida «{nombre}»; ignorada."
            )
            continue
        valores = [str(x).strip() for x in (e.get("valores") or []) if str(x).strip()]
        respuestas[nombre] = valores
        if str(e.get("confianza") or "") == "baja":
            nota = str(e.get("nota") or "").strip()
            detalle = f" — {nota}" if nota else ""
            incidencias.append(
                f"{caso}, «{nombre}»: confianza BAJA{detalle}; cotejar con la imagen."
            )

    for nombre in sorted(conocidas - respuestas.keys()):
        incidencias.append(f"{caso}: sin lectura para «{nombre}»; queda como perdido.")

    return respuestas, incidencias


def comparar_pasadas(
    a: dict[str, list[str]], b: dict[str, list[str]], *, caso: str
) -> list[str]:
    """Incidencias por discrepancia entre dos lecturas del mismo cuestionario.

    Se comparan los valores variable a variable; ante una discrepancia se
    conserva la primera lectura pero la incidencia señala ambas, que es lo que
    el revisor necesita para decidir mirando la imagen.
    """
    incidencias = []
    for nombre in sorted(set(a) | set(b)):
        va, vb = a.get(nombre, []), b.get(nombre, [])
        if va != vb:
            incidencias.append(
                f"{caso}, «{nombre}»: discrepancia entre pasadas ({va or 'vacío'} vs "
                f"{vb or 'vacío'}); verificar contra la imagen."
            )
    return incidencias


# --- Construcción de la matriz ----------------------------------------------


def _nombre_spss(nombre: str, usados: set[str]) -> str:
    """Adapta un nombre a las reglas de SPSS (sin espacios, sin empezar por dígito)."""
    limpio = re.sub(r"\W+", "_", nombre.strip(), flags=re.UNICODE).strip("_") or "var"
    if limpio[0].isdigit():
        limpio = f"v{limpio}"
    limpio = limpio[:60]
    base, n = limpio, 1
    while limpio.lower() in usados:
        n += 1
        limpio = f"{base}_{n}"
    usados.add(limpio.lower())
    return limpio


def _a_numero(texto: str) -> float | None:
    """Convierte '3', '3,5' o '3.5' a float; `None` si no es un número."""
    if not _NUMERO_RE.fullmatch(texto.strip()):
        return None
    return float(texto.strip().replace(",", "."))


@dataclass
class Matriz:
    """La matriz construida y sus metadatos para escribir el .sav."""

    df: pd.DataFrame
    etiquetas_variable: dict[str, str]
    etiquetas_valor: dict[str, dict[float, str]]
    medidas: dict[str, str]
    incidencias: list[str]


def construir_matriz(
    variables: list[Variable],
    casos: list[tuple[str, dict[str, list[str]]]],
    incidencias_previas: list[str] | None = None,
) -> Matriz:
    """Convierte las respuestas por caso en la matriz final con metadatos SPSS.

    - `unica`: una columna con el código marcado (numérica si los códigos lo
      son, con sus etiquetas de valor). Dos marcas => perdido + incidencia.
    - `multiple`: una columna 0/1 por opción (`p5_1`, `p5_2`, …); sin contestar
      => todas perdidas, no ceros.
    - `numerica`: el número escrito; lo no numérico => perdido + incidencia.
    - `abierta`: el texto literal.
    """
    incidencias = list(incidencias_previas or [])
    usados = {"caso"}

    # Plan de columnas: (columna, variable, código de opción para las múltiples).
    plan: list[tuple[str, Variable, str | None]] = []
    for v in variables:
        if v.tipo == "multiple":
            for codigo in v.opciones:
                plan.append((_nombre_spss(f"{v.nombre}_{codigo}", usados), v, codigo))
        else:
            plan.append((_nombre_spss(v.nombre, usados), v, None))

    filas: list[dict[str, object]] = []
    for id_caso, respuestas in casos:
        fila: dict[str, object] = {"caso": id_caso}
        for columna, v, codigo in plan:
            valores = respuestas.get(v.nombre)
            if v.tipo == "multiple":
                # Sin ninguna marca no se distingue «no eligió ninguna» de
                # «saltó la pregunta»: todas las opciones quedan perdidas.
                fila[columna] = (
                    None if not valores else (1.0 if codigo in valores else 0.0)
                )
            elif v.tipo == "numerica":
                fila[columna] = _leer_numerica(v, valores, id_caso, incidencias)
            elif v.tipo == "unica":
                fila[columna] = _leer_unica(v, valores, id_caso, incidencias)
            else:
                fila[columna] = " ; ".join(valores) if valores else None
        filas.append(fila)

    df = pd.DataFrame(filas, columns=["caso"] + [c for c, _, _ in plan])

    etiquetas_variable: dict[str, str] = {"caso": "Identificador del cuestionario"}
    etiquetas_valor: dict[str, dict[float, str]] = {}
    medidas: dict[str, str] = {"caso": "nominal"}
    for columna, v, codigo in plan:
        if v.tipo == "multiple":
            etiquetas_variable[columna] = f"{v.pregunta} — {v.opciones[codigo]}".strip(" —")
            etiquetas_valor[columna] = {0.0: "No marcada", 1.0: "Marcada"}
            medidas[columna] = "nominal"
        else:
            etiquetas_variable[columna] = v.pregunta
            if v.tipo == "unica" and v.codigos_numericos():
                etiquetas_valor[columna] = {
                    _a_numero(c): e for c, e in v.opciones.items()
                }
            medidas[columna] = {
                "unica": "nominal",
                "numerica": "scale",
                "abierta": "nominal",
            }[v.tipo]

    return Matriz(
        df=df,
        etiquetas_variable=etiquetas_variable,
        etiquetas_valor=etiquetas_valor,
        medidas=medidas,
        incidencias=incidencias,
    )


def _leer_numerica(
    v: Variable, valores: list[str] | None, caso: str, incidencias: list[str]
) -> float | None:
    if not valores:
        return None
    numero = _a_numero(valores[0])
    if numero is None:
        incidencias.append(
            f"{caso}, «{v.nombre}»: se leyó «{valores[0]}», que no es un número; "
            "queda como perdido."
        )
    return numero


def _leer_unica(
    v: Variable, valores: list[str] | None, caso: str, incidencias: list[str]
) -> object:
    if not valores:
        return None
    if len(valores) > 1:
        incidencias.append(
            f"{caso}, «{v.nombre}»: {len(valores)} opciones marcadas en una pregunta "
            f"de respuesta única ({valores}); queda como perdido."
        )
        return None
    valor = valores[0]
    if valor not in v.opciones:
        incidencias.append(
            f"{caso}, «{v.nombre}»: el código leído «{valor}» no está en el codebook; "
            "queda como perdido."
        )
        return None
    return _a_numero(valor) if v.codigos_numericos() else valor


def escribir_salidas(matriz: Matriz, base: Path) -> list[Path]:
    """Escribe `<base>.sav`, `<base>.csv` y `<base>.incidencias.txt`."""
    import pyreadstat

    salidas = []
    ruta_csv = base.with_suffix(".csv")
    matriz.df.to_csv(ruta_csv, index=False)
    salidas.append(ruta_csv)

    ruta_sav = base.with_suffix(".sav")
    pyreadstat.write_sav(
        matriz.df,
        str(ruta_sav),
        column_labels=matriz.etiquetas_variable,
        variable_value_labels=matriz.etiquetas_valor,
        variable_measure=matriz.medidas,
    )
    salidas.append(ruta_sav)

    ruta_inc = base.with_suffix(".incidencias.txt")
    contenido = (
        "\n".join(matriz.incidencias) + "\n"
        if matriz.incidencias
        else "Sin incidencias.\n"
    )
    ruta_inc.write_text(contenido, encoding="utf-8")
    salidas.append(ruta_inc)
    return salidas


# --- Visión (Claude) --------------------------------------------------------


class ExtractorVision:
    """Encapsula las llamadas a Claude (visión) con salida estructurada."""

    def __init__(self, *, modelo: str | None = None, client: object | None = None) -> None:
        self._modelo = modelo or os.environ.get("ANTHROPIC_MODEL", MODELO_POR_DEFECTO)
        self._client = client

    def _get_client(self) -> object:
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise SystemExit(
                    "Falta ANTHROPIC_API_KEY: expórtala como variable de entorno "
                    "para usar la extracción por visión."
                )
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def _llamar(self, *, system: str, instruccion: str, rutas: list[Path], schema: dict) -> dict:
        contenido: list[dict] = []
        for ruta in rutas:
            media_type = _MEDIA_TYPES.get(ruta.suffix.lower())
            if media_type is None:
                raise SystemExit(
                    f"Formato de imagen no soportado: {ruta} "
                    f"(admitidos: {', '.join(sorted(_MEDIA_TYPES))})"
                )
            contenido.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(ruta.read_bytes()).decode("ascii"),
                    },
                }
            )
        contenido.append({"type": "text", "text": instruccion})

        response = self._get_client().messages.create(
            model=self._modelo,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": contenido}],
        )
        texto = "".join(b.text for b in response.content if b.type == "text").strip()
        return json.loads(texto) if texto else {}

    def codebook(self, rutas: list[Path]) -> dict:
        return self._llamar(
            system=_CODEBOOK_SYSTEM,
            instruccion=_CODEBOOK_INSTRUCTION,
            rutas=rutas,
            schema=_CODEBOOK_SCHEMA,
        )

    def caso(self, variables: list[Variable], rutas: list[Path]) -> dict:
        compacto = json.dumps(
            [
                {
                    "variable": v.nombre,
                    "pregunta": v.pregunta,
                    "tipo": v.tipo,
                    "opciones": v.opciones,
                }
                for v in variables
            ],
            ensure_ascii=False,
        )
        instruccion = (
            f"Libro de códigos del cuestionario:\n{compacto}\n\n"
            "Extrae las respuestas de este cuestionario cumplimentado. Devuelve "
            "una entrada por variable del libro de códigos."
        )
        return self._llamar(
            system=_EXTRACT_SYSTEM,
            instruccion=instruccion,
            rutas=rutas,
            schema=_EXTRACT_SCHEMA,
        )


# --- CLI --------------------------------------------------------------------


def _agrupar_paginas(rutas: list[Path], por_caso: int) -> list[list[Path]]:
    """Agrupa las imágenes en casos de `por_caso` páginas consecutivas."""
    if len(rutas) % por_caso != 0:
        print(
            f"Aviso: {len(rutas)} imágenes no son múltiplo de "
            f"--paginas-por-caso={por_caso}; el último caso irá incompleto.",
            file=sys.stderr,
        )
    return [rutas[i : i + por_caso] for i in range(0, len(rutas), por_caso)]


def cmd_codebook(args) -> int:
    rutas = [Path(r) for r in args.imagenes]
    extractor = ExtractorVision(modelo=args.modelo)
    variables, avisos = validar_codebook(extractor.codebook(rutas))
    doc = codebook_a_documento(variables, origen=[str(r) for r in rutas], avisos=avisos)
    salida = Path(args.salida)
    salida.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Codebook con {len(variables)} variables -> {salida}")
    for aviso in avisos:
        print(f"  aviso: {aviso}")
    print("Revísalo (nombres, códigos, opciones) antes de extraer.")
    return 0


def cmd_extraer(args) -> int:
    codebook_raw = json.loads(Path(args.codebook).read_text(encoding="utf-8"))
    variables, avisos = validar_codebook(codebook_raw)
    if not variables:
        print("El codebook no tiene variables válidas.", file=sys.stderr)
        return 1

    rutas = [Path(r) for r in args.imagenes]
    grupos = _agrupar_paginas(rutas, args.paginas_por_caso)
    extractor = ExtractorVision(modelo=args.modelo)

    casos: list[tuple[str, dict[str, list[str]]]] = []
    incidencias: list[str] = [f"codebook: {a}" for a in avisos]
    for grupo in grupos:
        id_caso = grupo[0].stem
        print(f"Extrayendo {id_caso} ({len(grupo)} página/s)…")
        respuestas, inc = parse_respuestas(
            extractor.caso(variables, grupo), variables, caso=id_caso
        )
        incidencias.extend(inc)
        if args.verificar:
            segunda, _ = parse_respuestas(
                extractor.caso(variables, grupo), variables, caso=id_caso
            )
            incidencias.extend(comparar_pasadas(respuestas, segunda, caso=id_caso))
        casos.append((id_caso, respuestas))

    matriz = construir_matriz(variables, casos, incidencias_previas=incidencias)
    salidas = escribir_salidas(matriz, Path(args.salida))
    print(
        f"\nMatriz: {len(matriz.df)} casos x {len(matriz.df.columns)} columnas"
        f" -> {', '.join(str(s) for s in salidas[:2])}"
    )
    if matriz.incidencias:
        print(f"{len(matriz.incidencias)} incidencias que revisar -> {salidas[2]}")
    else:
        print("Sin incidencias.")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cuestionario_a_matriz",
        description=(
            "Extrae con visión (Claude) los datos de cuestionarios en imagen y "
            "genera la matriz de datos (.sav + .csv) para SPSS."
        ),
    )
    parser.add_argument(
        "--modelo",
        default=None,
        help=f"modelo de Anthropic (por defecto $ANTHROPIC_MODEL o {MODELO_POR_DEFECTO})",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_cb = sub.add_parser(
        "codebook", help="redactar el libro de códigos desde la imagen del cuestionario"
    )
    p_cb.add_argument("imagenes", nargs="+", help="imagen(es) del cuestionario")
    p_cb.add_argument("--salida", default="codebook.json", help="JSON de salida")
    p_cb.set_defaults(func=cmd_codebook)

    p_ex = sub.add_parser(
        "extraer", help="extraer las respuestas y construir la matriz"
    )
    p_ex.add_argument("codebook", help="codebook.json ya revisado")
    p_ex.add_argument("imagenes", nargs="+", help="imágenes de cuestionarios cumplimentados")
    p_ex.add_argument(
        "--paginas-por-caso",
        type=int,
        default=1,
        help="imágenes consecutivas que forman un mismo cuestionario (por defecto 1)",
    )
    p_ex.add_argument(
        "--verificar",
        action="store_true",
        help="leer cada cuestionario dos veces y señalar las discrepancias (dobla el coste)",
    )
    p_ex.add_argument("--salida", default="matriz", help="base de los archivos de salida")
    p_ex.set_defaults(func=cmd_extraer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
