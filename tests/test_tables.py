"""Tests de las tablas: linealización, ingesta, recuperación y cita."""

from app.models.schemas import TableRow, TableSpec
from app.services.table_service import linearize_table
from tests.samples import SAMPLE_ORDENANZA

TABLA_RUIDO = {
    "tabla": "Tabla 1",
    "articulo": "10",
    "descripcion": "Objetivos de calidad acústica para ruido ambiental exterior",
    "columnas": [
        "Ld (índice de ruido, periodo día)",
        "Le (índice de ruido, periodo tarde)",
        "Ln (índice de ruido, periodo noche)",
    ],
    "unidad": "dB",
    "filas": [
        {"clave": "Sectores con predominio de suelo de uso residencial",
         "valores": ["65", "65", "55"]},
        {"clave": "Sectores con predominio de suelo de uso industrial",
         "valores": ["75", "75", "65"]},
    ],
}


def _crear_con_tabla(client):
    resp = client.post(
        "/ordenanzas",
        json={
            "titulo": "Ordenanza de Contaminación Acústica",
            "contenido": SAMPLE_ORDENANZA,
            "tablas": [TABLA_RUIDO],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Linealización (unidad) --------------------------------------------------

def test_linearize_una_fila_por_registro():
    spec = TableSpec(
        tabla="Tabla 1",
        articulo="10",
        descripcion="Objetivos de calidad acústica exterior",
        columnas=["Ld (día)", "Ln (noche)"],
        unidad="dB",
        filas=[TableRow(clave="Uso residencial", valores=["65", "55"])],
    )
    fragmentos = linearize_table(spec)
    assert len(fragmentos) == 1
    frag = fragmentos[0]
    assert frag.articulo == "10"
    assert frag.tabla == "Tabla 1"
    assert "Uso residencial" in frag.texto
    assert "Ld (día): 65 dB" in frag.texto
    assert "Ln (noche): 55 dB" in frag.texto


def test_linearize_tolera_valores_incompletos():
    spec = TableSpec(
        tabla="T",
        articulo="1",
        descripcion="d",
        columnas=["a", "b", "c"],
        unidad="",
        filas=[TableRow(clave="k", valores=["1", "2"])],
    )
    frag = linearize_table(spec)[0]
    assert "c: s/d" in frag.texto


# --- Ingesta + recuperación (integración) ------------------------------------

def test_alta_con_tabla_cuenta_articulos_y_filas(client):
    creada = _crear_con_tabla(client)
    assert creada["articulo_count"] == 5
    assert creada["tabla_count"] == 1
    # 5 artículos + 2 filas de tabla.
    assert creada["chunk_count"] == 7


def test_recupera_fila_de_tabla_por_lenguaje_natural(client):
    _crear_con_tabla(client)
    resp = client.post(
        "/consultas/buscar",
        json={"pregunta": "límite de ruido en zona residencial por la noche"},
    )
    assert resp.status_code == 200
    filas_tabla = [c for c in resp.json()["resultados"] if c["tipo"] == "tabla"]
    assert filas_tabla, "debería recuperar al menos una fila de tabla"
    residencial = next(
        (c for c in filas_tabla if "residencial" in c["texto"].lower()), None
    )
    assert residencial is not None
    assert residencial["tabla"] == "Tabla 1"
    assert residencial["articulo"] == "10"
    assert "55" in residencial["texto"]  # el valor de la columna 'noche'


def test_consulta_incluye_la_tabla_en_el_contexto_citable(client, llm):
    _crear_con_tabla(client)
    resp = client.post(
        "/consultas",
        json={"pregunta": "¿límite de ruido en zona residencial de noche?"},
    )
    assert resp.status_code == 200
    contexto = "\n".join(llm.calls[-1]["contexto"])
    assert "Tabla 1 (Artículo 10)" in contexto
