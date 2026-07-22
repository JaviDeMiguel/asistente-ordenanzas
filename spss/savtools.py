#!/usr/bin/env python3
"""CLI para automatizar tareas con archivos de datos de IBM SPSS (.sav) sin abrir SPSS.

Lee los .sav con pyreadstat (no requiere licencia ni instalación de SPSS) y ofrece:

    info      resumen del archivo: casos, variables, etiquetas y niveles de medida
    exportar  volcado de los datos a CSV o Excel, con o sin etiquetas de valor
    informe   informe Excel por archivo: diccionario de variables, descriptivos
              de las numéricas y frecuencias de las categóricas

Todos los subcomandos aceptan varios .sav a la vez, así sirven para lotes:

    python savtools.py info encuesta_2024.sav encuesta_2025.sav
    python savtools.py exportar datos/*.sav --formato xlsx --etiquetas
    python savtools.py informe encuesta.sav --salida informe_encuesta.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pyreadstat

# Niveles de medida de SPSS que tratamos como categóricos en el informe.
MEDIDAS_CATEGORICAS = {"nominal", "ordinal"}


def leer_sav(ruta: Path, aplicar_etiquetas: bool = False):
    """Lee un .sav y devuelve (DataFrame, metadatos de SPSS)."""
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo: {ruta}")
    return pyreadstat.read_sav(str(ruta), apply_value_formats=aplicar_etiquetas)


def diccionario_variables(meta) -> pd.DataFrame:
    """Diccionario de variables: nombre, etiqueta, medida y etiquetas de valor."""
    filas = []
    for nombre in meta.column_names:
        etiquetas_valor = meta.variable_value_labels.get(nombre, {})
        filas.append(
            {
                "variable": nombre,
                "etiqueta": meta.column_names_to_labels.get(nombre) or "",
                "medida": meta.variable_measure.get(nombre, ""),
                "etiquetas_de_valor": "; ".join(
                    f"{valor}={texto}" for valor, texto in etiquetas_valor.items()
                ),
            }
        )
    return pd.DataFrame(filas)


def es_categorica(nombre: str, serie: pd.Series, meta) -> bool:
    """Categórica si SPSS la declara nominal/ordinal o si tiene etiquetas de valor."""
    if meta.variable_measure.get(nombre) in MEDIDAS_CATEGORICAS:
        return True
    return nombre in meta.variable_value_labels


def tabla_frecuencias(nombre: str, serie: pd.Series, meta) -> pd.DataFrame:
    """Frecuencias de una variable con su etiqueta de valor y porcentaje."""
    etiquetas = meta.variable_value_labels.get(nombre, {})
    conteo = serie.value_counts(dropna=False).sort_index()
    total = len(serie)
    return pd.DataFrame(
        {
            "variable": nombre,
            "valor": conteo.index,
            "etiqueta": [etiquetas.get(valor, "") for valor in conteo.index],
            "n": conteo.values,
            "porcentaje": (conteo.values / total * 100).round(1),
        }
    )


def cmd_info(rutas: list[Path], args) -> None:
    for ruta in rutas:
        df, meta = leer_sav(ruta)
        print(f"\n=== {ruta} ===")
        print(f"Casos: {meta.number_rows}   Variables: {meta.number_columns}")
        dic = diccionario_variables(meta)
        with pd.option_context("display.max_rows", None, "display.width", 120):
            print(dic.to_string(index=False))


def cmd_exportar(rutas: list[Path], args) -> None:
    for ruta in rutas:
        df, _ = leer_sav(ruta, aplicar_etiquetas=args.etiquetas)
        salida = Path(args.salida) if args.salida else ruta.with_suffix(f".{args.formato}")
        if args.formato == "csv":
            df.to_csv(salida, index=False)
        else:
            df.to_excel(salida, index=False)
        print(f"{ruta} -> {salida}  ({len(df)} filas, {len(df.columns)} columnas)")


def cmd_informe(rutas: list[Path], args) -> None:
    for ruta in rutas:
        df, meta = leer_sav(ruta)
        salida = Path(args.salida) if args.salida else ruta.with_name(f"informe_{ruta.stem}.xlsx")

        categoricas = [c for c in df.columns if es_categorica(c, df[c], meta)]
        numericas = [
            c
            for c in df.columns
            if c not in categoricas and pd.api.types.is_numeric_dtype(df[c])
        ]

        with pd.ExcelWriter(salida) as escritor:
            diccionario_variables(meta).to_excel(escritor, sheet_name="Variables", index=False)
            if numericas:
                descriptivos = df[numericas].describe().T.reset_index(names="variable")
                descriptivos.to_excel(escritor, sheet_name="Descriptivos", index=False)
            if categoricas:
                frecuencias = pd.concat(
                    [tabla_frecuencias(c, df[c], meta) for c in categoricas],
                    ignore_index=True,
                )
                frecuencias.to_excel(escritor, sheet_name="Frecuencias", index=False)
        print(
            f"{ruta} -> {salida}  "
            f"({len(numericas)} numéricas, {len(categoricas)} categóricas)"
        )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="savtools",
        description="Automatiza tareas sobre archivos .sav de IBM SPSS sin abrir SPSS.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_info = sub.add_parser("info", help="resumen y diccionario de variables")
    p_info.add_argument("archivos", nargs="+", help="uno o más .sav")
    p_info.set_defaults(func=cmd_info)

    p_exp = sub.add_parser("exportar", help="exportar los datos a CSV o Excel")
    p_exp.add_argument("archivos", nargs="+", help="uno o más .sav")
    p_exp.add_argument("--formato", choices=["csv", "xlsx"], default="csv")
    p_exp.add_argument(
        "--etiquetas",
        action="store_true",
        help="sustituir los códigos por sus etiquetas de valor (1 -> 'Mujer')",
    )
    p_exp.add_argument("--salida", help="ruta de salida (solo con un único archivo)")
    p_exp.set_defaults(func=cmd_exportar)

    p_inf = sub.add_parser("informe", help="informe Excel con descriptivos y frecuencias")
    p_inf.add_argument("archivos", nargs="+", help="uno o más .sav")
    p_inf.add_argument("--salida", help="ruta del .xlsx (solo con un único archivo)")
    p_inf.set_defaults(func=cmd_informe)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    rutas = [Path(a) for a in args.archivos]
    if getattr(args, "salida", None) and len(rutas) > 1:
        print("--salida solo puede usarse con un único archivo", file=sys.stderr)
        return 2
    try:
        args.func(rutas, args)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
