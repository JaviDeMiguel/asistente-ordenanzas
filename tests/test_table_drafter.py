"""Tests del drafter de tablas por visión (offline, con doble del cliente de visión).

El modelo de visión (Claude) se sustituye por un doble que devuelve un JSON
prefijado, igual que `RecordingLLM` sustituye a `LLMService`. El render y la
extracción de texto del PDF se inyectan / monkeypatchean, de modo que ningún test
toca la red, el disco ni depende de `pypdfium2`.
"""

import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models.schemas import TableRow, TableSpec
from app.services.llm_service import LLMConfigurationError
from app.services.table_drafter import (
    TableDraft,
    TableDrafter,
    _compare_specs,
    _slug,
    draft_tables,
    expand_selection,
    find_table_pages,
    looks_like_table,
    merge_boxes,
    parse_table_drafts,
    reconcile_drafts,
)

# --- Datos y dobles ----------------------------------------------------------

_TABLA_OK = {
    "tabla": "Tabla 1",
    "articulo": "10",
    "descripcion": "Objetivos de calidad acústica",
    "unidad": "dB",
    "columnas": ["Ld (día)", "Le (tarde)", "Ln (noche)"],
    "filas": [
        {"clave": "Uso residencial", "valores": ["65", "65", "55"]},
        {"clave": "Uso industrial", "valores": ["75", "75", "65"]},
    ],
    "confianza": "alta",
    "nota": "",
}


class _FakeMessages:
    """Registra las llamadas y devuelve JSON prefijado como bloque de texto.

    `payload` puede ser un dict/str fijo o una lista de payloads que se van
    consumiendo en cada llamada (para simular pasadas de visión discrepantes).
    """

    def __init__(self, payload) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        payload = self._payload
        if isinstance(payload, list):
            payload = payload[min(len(self.calls) - 1, len(payload) - 1)]
        text = payload if isinstance(payload, str) else json.dumps(payload)
        blocks = [SimpleNamespace(type="thinking", text="")]
        if text:
            blocks.append(SimpleNamespace(type="text", text=text))
        return SimpleNamespace(content=blocks)


class _FakeClient:
    def __init__(self, payload) -> None:
        self.messages = _FakeMessages(payload)


def _settings() -> Settings:
    return Settings(anthropic_api_key=None, drafter_max_tokens=1024, drafter_render_scale=2.0)


def _draft(*, tabla="Tabla 1", columnas=("Ln",), filas=(("res", ("55",)),), confianza="alta"):
    return TableDraft(
        spec=TableSpec(
            tabla=tabla,
            articulo="10",
            descripcion="d",
            columnas=list(columnas),
            unidad="dB",
            filas=[TableRow(clave=k, valores=list(v)) for k, v in filas],
        ),
        pagina=3,
        confianza=confianza,
    )


# --- find_table_pages --------------------------------------------------------

def test_find_table_pages_detecta_tablas_cuadros_arabigos_y_romanos():
    textos = [
        "Portada de la ordenanza",
        "Artículo 10. Se aplican los valores de la Tabla 1.",
        # 'tabla de contenidos' NO debe contar (la 'd' de 'de' es romana pero no
        # queda en frontera de palabra); 'tabla siguiente' tampoco.
        "La tabla siguiente; véase la tabla de contenidos.",
        "Artículo 22. Ver TABLA 5, el Cuadro 2 y la Tabla V.",
    ]
    assert find_table_pages(textos) == [1, 3]


def test_find_table_pages_sin_tablas_devuelve_lista_vacia():
    assert find_table_pages(["texto sin tablas", "", None]) == []


def test_slug_saneado():
    assert _slug("Tabla 3") == "Tabla-3"
    assert _slug("  ") == "tabla"


# --- looks_like_table / merge_boxes / expand_selection (geometría) -----------

def test_looks_like_table():
    assert looks_like_table(200, 50, 595) is True
    assert looks_like_table(50, 50, 595) is False   # demasiado estrecha
    assert looks_like_table(300, 10, 595) is False  # demasiado baja


def test_merge_boxes_fusiona_cercanas_y_separa_lejanas():
    # Dos cajas casi tocándose (hueco 1 pt) se fusionan con gap 6.
    assert merge_boxes([(0, 0, 10, 10), (11, 0, 20, 10)], gap=6) == [(0, 0, 20, 10)]
    # Con gap pequeño (0.5) el hueco de 1 pt las mantiene separadas.
    assert sorted(merge_boxes([(0, 0, 10, 10), (11, 0, 20, 10)], gap=0.5)) == [
        (0, 0, 10, 10),
        (11, 0, 20, 10),
    ]
    # Cajas solapadas siempre se fusionan.
    assert merge_boxes([(0, 0, 10, 10), (5, 5, 15, 15)], gap=0) == [(0, 0, 15, 15)]


def test_expand_selection_une_leyenda_con_imagenes_contiguas():
    # caption 0-based [2,3,4,6]; imagen [0,3,6,7,16] -> añade la 7 (junto a la 6),
    # ignora la 0 (portada) y la 16 (anexo), lejos de toda leyenda.
    assert expand_selection([2, 3, 4, 6], [0, 3, 6, 7, 16]) == [2, 3, 4, 6, 7]


# --- parse_table_drafts ------------------------------------------------------

def test_parse_table_drafts_valida_y_envuelve():
    borradores, avisos = parse_table_drafts({"tablas": [_TABLA_OK]}, pagina=3)
    assert avisos == []
    assert len(borradores) == 1
    draft = borradores[0]
    assert draft.spec.tabla == "Tabla 1"
    assert draft.spec.articulo == "10"
    assert draft.pagina == 3
    assert draft.confianza == "alta"
    assert [f.clave for f in draft.spec.filas] == ["Uso residencial", "Uso industrial"]


def test_parse_table_drafts_acepta_lista_directa():
    borradores, _ = parse_table_drafts([_TABLA_OK], pagina=1)
    assert len(borradores) == 1


def test_parse_table_drafts_descarta_entrada_invalida():
    mala = {"tabla": "Tabla X", "articulo": "9"}  # faltan columnas/filas
    borradores, avisos = parse_table_drafts({"tablas": [mala]}, pagina=2)
    assert borradores == []
    assert len(avisos) == 1
    assert "descartada" in avisos[0]


def test_parse_table_drafts_descarta_no_dict():
    borradores, avisos = parse_table_drafts({"tablas": ["no es un objeto"]}, pagina=4)
    assert borradores == []
    assert "formato inesperado" in avisos[0]


def test_parse_table_drafts_avisa_desalineacion_de_columnas():
    desalineada = {
        **_TABLA_OK,
        "filas": [{"clave": "Uso residencial", "valores": ["65", "55"]}],  # 2 != 3
    }
    borradores, avisos = parse_table_drafts({"tablas": [desalineada]}, pagina=5)
    assert len(borradores) == 1  # se conserva, pero se avisa
    assert any("columnas; revisar" in a for a in avisos)


def test_parse_table_drafts_confianza_baja_genera_aviso():
    dudosa = {**_TABLA_OK, "confianza": "baja", "nota": "el 55 está borroso"}
    _, avisos = parse_table_drafts({"tablas": [dudosa]}, pagina=6)
    assert any("confianza BAJA" in a and "el 55 está borroso" in a for a in avisos)


def test_parse_table_drafts_entrada_no_estructurada():
    borradores, avisos = parse_table_drafts("no es json de tablas", pagina=1)
    assert borradores == []
    assert avisos == []


# --- reconcile_drafts (verificación cruzada) ---------------------------------

def test_reconcile_drafts_sin_discrepancias_conserva_confianza():
    d1, d2 = _draft(), _draft()
    reconciliados, avisos = reconcile_drafts([[d1], [d2]], pagina=3)
    assert avisos == []
    assert reconciliados[0].confianza == "alta"


def test_reconcile_drafts_discrepancia_de_valor_baja_confianza():
    d1 = _draft(filas=(("res", ("55",)),))
    d2 = _draft(filas=(("res", ("56",)),))  # una lectura distinta del mismo número
    reconciliados, avisos = reconcile_drafts([[d1], [d2]], pagina=3)
    assert reconciliados[0].confianza == "baja"
    assert "discrepancia de valores entre pasadas" in reconciliados[0].nota
    assert any("'55' vs '56'" in a for a in avisos)


def test_reconcile_drafts_ignora_diferencias_de_nombre():
    # Mismos valores; solo cambian el nombre de columna (Ld vs L_d) y la etiqueta
    # de fila ('a - res' vs 'a res'). No debe bajar la confianza ni avisar.
    d1 = _draft(tabla="Tabla 1", columnas=("Ld",), filas=(("a - res", ("55",)),))
    d2 = _draft(tabla="Tabla 1", columnas=("L_d",), filas=(("a res", ("55",)),))
    reconciliados, avisos = reconcile_drafts([[d1], [d2]], pagina=3)
    assert reconciliados[0].confianza == "alta"
    assert avisos == []


def test_reconcile_drafts_estructura_difiere_sin_conflicto_de_valor():
    # Una pasada trae una fila menos; los valores comparables coinciden -> nota de
    # estructura, sin bajar la confianza.
    d1 = _draft(columnas=("Ld",), filas=(("r1", ("55",)), ("r2", ("60",))))
    d2 = _draft(columnas=("Ld",), filas=(("r1", ("55",)),))
    reconciliados, avisos = reconcile_drafts([[d1], [d2]], pagina=3)
    assert reconciliados[0].confianza == "alta"
    assert any("estructura difiere" in a and "filas 2 vs 1" in a for a in avisos)


def test_reconcile_drafts_numero_de_tablas_difiere():
    d1 = _draft(tabla="Tabla 1")
    reconciliados, avisos = reconcile_drafts([[d1], []], pagina=3)
    assert reconciliados[0].confianza == "alta"  # la pasada 1 la leyó bien
    assert any("nº de tablas" in a for a in avisos)


def test_reconcile_drafts_sin_pasadas():
    assert reconcile_drafts([], pagina=1) == ([], [])


def test_compare_specs_separa_valores_de_estructura():
    a = _draft(columnas=("Ld", "Le"), filas=(("r1", ("55", "65")),)).spec
    b = _draft(columnas=("Ld",), filas=(("r1", ("56",)),)).spec
    conflictos, notas = _compare_specs(a, b)
    assert any("'55' vs '56'" in c for c in conflictos)  # conflicto de valor
    assert any("columnas" in n for n in notas)           # nota de estructura


def test_compare_specs_normaliza_espacios_y_mayusculas():
    a = _draft(filas=(("r", ("S/D",)),)).spec
    b = _draft(filas=(("r", (" s/d ",)),)).spec
    conflictos, _ = _compare_specs(a, b)
    assert conflictos == []


# --- draft_tables (orquestación) ---------------------------------------------

def test_draft_tables_orquesta_regiones_y_paginas():
    render_calls: list[int] = []

    def fake_render(idx: int) -> list[bytes]:
        render_calls.append(idx)
        return [b"PNG-A", b"PNG-B"]  # dos tablas (regiones) por página

    def fake_transcribe(png: bytes, idx: int):
        etiqueta = "Tabla A" if png == b"PNG-A" else "Tabla B"
        return {"tablas": [{**_TABLA_OK, "tabla": etiqueta}]}

    resultado = draft_tables(
        indices=[1, 3],
        render_regions=fake_render,
        transcribe_page=fake_transcribe,
    )
    assert render_calls == [1, 3]
    assert resultado.paginas == [2, 4]  # 1-based
    assert [d.spec.tabla for d in resultado.borradores] == [
        "Tabla A", "Tabla B", "Tabla A", "Tabla B",
    ]


def test_draft_tables_con_verificacion_marca_discrepancias():
    # La misma región se transcribe dos veces con un valor distinto -> baja + aviso.
    payloads = [
        {"tablas": [{**_TABLA_OK, "filas": [{"clave": "res", "valores": ["55"]}],
                     "columnas": ["Ln"]}]},
        {"tablas": [{**_TABLA_OK, "filas": [{"clave": "res", "valores": ["56"]}],
                     "columnas": ["Ln"]}]},
    ]
    contador = {"n": 0}

    def fake_transcribe(png: bytes, idx: int):
        payload = payloads[contador["n"] % 2]
        contador["n"] += 1
        return payload

    resultado = draft_tables(
        indices=[0],
        render_regions=lambda i: [b"PNG"],
        transcribe_page=fake_transcribe,
        passes=2,
    )
    assert len(resultado.borradores) == 1
    assert resultado.borradores[0].confianza == "baja"
    assert any("discrepancia" in a for a in resultado.avisos)


def test_result_to_document_incluye_tablas_listas_para_publicar():
    resultado = draft_tables(
        indices=[2],
        render_regions=lambda i: [b"x"],
        transcribe_page=lambda png, i: {"tablas": [_TABLA_OK]},
    )
    doc = resultado.to_document()
    assert doc["revisar_antes_de_indexar"] is True
    assert doc["paginas_analizadas"] == [3]
    assert doc["borradores"][0]["pagina"] == 3
    assert doc["borradores"][0]["confianza"] == "alta"
    assert "imagen" not in doc["borradores"][0]  # sin imagenes_dir no se añade
    # `tablas` reproduce el esquema del alta (TableSpec), sin metadatos de revisión.
    assert doc["tablas"][0]["tabla"] == "Tabla 1"
    assert set(doc["tablas"][0]) == {
        "tabla", "articulo", "descripcion", "columnas", "unidad", "filas",
    }


def test_draft_tables_adjunta_el_recorte_a_cada_borrador():
    resultado = draft_tables(
        indices=[2],
        render_regions=lambda i: [b"PNG-DE-LA-TABLA"],
        transcribe_page=lambda png, i: {"tablas": [_TABLA_OK]},
    )
    assert resultado.borradores[0].imagen == b"PNG-DE-LA-TABLA"


def test_crops_y_to_document_referencian_los_recortes():
    resultado = draft_tables(
        indices=[2],
        render_regions=lambda i: [b"PNG"],
        transcribe_page=lambda png, i: {"tablas": [_TABLA_OK]},
    )
    ((nombre, datos),) = resultado.crops()
    assert datos == b"PNG"
    assert nombre == "p3_01_Tabla-1.png"
    doc = resultado.to_document(imagenes_dir="recortes")
    assert doc["borradores"][0]["imagen"] == "recortes/p3_01_Tabla-1.png"


# --- TableDrafter (integración de las piezas, con cliente falso) --------------

def test_transcribe_page_usa_vision_y_devuelve_json():
    client = _FakeClient({"tablas": [_TABLA_OK]})
    drafter = TableDrafter(_settings(), client=client)
    raw = drafter._transcribe_page(b"PNG", "Artículo 10 ... Tabla 1", 0)
    assert raw == {"tablas": [_TABLA_OK]}
    (call,) = client.messages.calls
    contenido = call["messages"][0]["content"]
    assert contenido[0]["type"] == "image"
    assert contenido[0]["source"]["media_type"] == "image/png"
    assert "output_config" in call and call["thinking"] == {"type": "adaptive"}
    assert "Artículo 10" in contenido[1]["text"]  # el texto de la página va de contexto


def test_transcribe_page_sin_texto_devuelve_tablas_vacias():
    client = _FakeClient("")  # el modelo no devuelve texto
    drafter = TableDrafter(_settings(), client=client)
    assert drafter._transcribe_page(b"PNG", "", 0) == {"tablas": []}


def test_get_client_sin_clave_lanza():
    drafter = TableDrafter(_settings())  # sin cliente inyectado ni clave
    with pytest.raises(LLMConfigurationError):
        drafter._get_client()


def test_draft_from_pdf_autoselecciona_paginas(monkeypatch):
    textos = ["Portada", "Artículo 10. Ver la Tabla 1.", "Sin tablas"]
    monkeypatch.setattr("app.services.table_drafter._extract_page_texts", lambda pdf: textos)
    # La pág. 0-based 2 tiene imagen-tabla y es contigua a la 1 (con leyenda).
    monkeypatch.setattr("app.services.table_drafter._image_table_pages", lambda pdf: [2])
    client = _FakeClient({"tablas": [_TABLA_OK]})
    drafter = TableDrafter(_settings(), client=client, render_regions=lambda pdf, i: [b"PNG"])
    resultado = drafter.draft_from_pdf(b"%PDF-fake")
    assert resultado.paginas == [2, 3]  # leyenda (pág.2) + imagen contigua (pág.3)
    assert len(resultado.borradores) == 2


def test_draft_from_pdf_fallback_a_imagenes_sin_leyenda(monkeypatch):
    # Sin leyendas «Tabla N» reconocibles (p. ej. PDF escaneado): se analizan las
    # páginas con imagen-tabla.
    monkeypatch.setattr(
        "app.services.table_drafter._extract_page_texts", lambda pdf: ["sin", "leyendas"]
    )
    monkeypatch.setattr("app.services.table_drafter._image_table_pages", lambda pdf: [0, 1])
    drafter = TableDrafter(
        _settings(), client=_FakeClient({"tablas": []}), render_regions=lambda pdf, i: [b"PNG"]
    )
    resultado = drafter.draft_from_pdf(b"%PDF")
    assert resultado.paginas == [1, 2]


def test_draft_from_pdf_sin_tablas_ni_imagenes_vacio(monkeypatch):
    monkeypatch.setattr("app.services.table_drafter._extract_page_texts", lambda pdf: ["texto"])
    monkeypatch.setattr("app.services.table_drafter._image_table_pages", lambda pdf: [])
    drafter = TableDrafter(
        _settings(), client=_FakeClient({"tablas": []}), render_regions=lambda pdf, i: [b"PNG"]
    )
    resultado = drafter.draft_from_pdf(b"%PDF")
    assert resultado.paginas == []
    assert resultado.borradores == []


def test_draft_from_pdf_paginas_forzadas_usa_render_por_defecto(monkeypatch):
    textos = ["p0", "p1", "p2", "p3"]
    monkeypatch.setattr("app.services.table_drafter._extract_page_texts", lambda pdf: textos)
    # Cubre la rama de render por defecto (`_regions` sin inyectar) parcheando el
    # renderizador de bajo nivel; el cliente de visión sigue siendo un doble.
    render_calls: list[int] = []

    def fake_render_regions(pdf, i, *, scale, margin_side, margin_top):
        render_calls.append(i)
        return [b"PNG"]

    monkeypatch.setattr("app.services.table_drafter._render_regions", fake_render_regions)
    client = _FakeClient({"tablas": [_TABLA_OK]})
    drafter = TableDrafter(_settings(), client=client)  # sin render_regions inyectado
    resultado = drafter.draft_from_pdf(b"%PDF", paginas=[3, 99])  # 99 fuera de rango
    assert render_calls == [2]  # solo la pág. 3 (índice 2)
    assert resultado.paginas == [3]
