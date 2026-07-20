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
from app.services.llm_service import LLMConfigurationError
from app.services.table_drafter import (
    TableDrafter,
    draft_tables,
    find_table_pages,
    parse_table_drafts,
)

# --- Dobles del cliente de Anthropic -----------------------------------------

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
    """Registra las llamadas y devuelve un JSON prefijado como bloque de texto."""

    def __init__(self, payload) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def create(self, **kwargs) -> SimpleNamespace:
        self.calls.append(kwargs)
        text = json.dumps(self._payload) if not isinstance(self._payload, str) else self._payload
        blocks = [SimpleNamespace(type="thinking", text="")]
        if text:
            blocks.append(SimpleNamespace(type="text", text=text))
        return SimpleNamespace(content=blocks)


class _FakeClient:
    def __init__(self, payload) -> None:
        self.messages = _FakeMessages(payload)


def _settings() -> Settings:
    return Settings(anthropic_api_key=None, drafter_max_tokens=1024, drafter_render_scale=2.0)


# --- find_table_pages --------------------------------------------------------

def test_find_table_pages_detecta_solo_paginas_con_tabla_numerada():
    textos = [
        "Portada de la ordenanza",
        "Artículo 10. Se aplican los valores de la Tabla 1.",
        "La tabla siguiente ya no lleva número aquí.",
        "Artículo 22. Ver TABLA 5 y la tabla nº 6.",
    ]
    assert find_table_pages(textos) == [1, 3]


def test_find_table_pages_sin_tablas_devuelve_lista_vacia():
    assert find_table_pages(["texto sin tablas", "", None]) == []


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


# --- draft_tables (orquestación) ---------------------------------------------

def test_draft_tables_orquesta_con_dobles():
    render_calls: list[int] = []

    def fake_render(idx: int) -> bytes:
        render_calls.append(idx)
        return b"PNG-FAKE"

    def fake_transcribe(png: bytes, idx: int):
        assert png == b"PNG-FAKE"
        return {"tablas": [{**_TABLA_OK, "articulo": str(idx)}]}

    resultado = draft_tables(
        indices=[1, 3],
        render_page=fake_render,
        transcribe_page=fake_transcribe,
    )
    assert render_calls == [1, 3]
    assert resultado.paginas == [2, 4]  # 1-based
    assert [d.spec.articulo for d in resultado.borradores] == ["1", "3"]


def test_result_to_document_incluye_tablas_listas_para_publicar():
    resultado = draft_tables(
        indices=[2],
        render_page=lambda i: b"x",
        transcribe_page=lambda png, i: {"tablas": [_TABLA_OK]},
    )
    doc = resultado.to_document()
    assert doc["revisar_antes_de_indexar"] is True
    assert doc["paginas_analizadas"] == [3]
    assert doc["borradores"][0]["pagina"] == 3
    assert doc["borradores"][0]["confianza"] == "alta"
    # `tablas` reproduce el esquema del alta (TableSpec), sin metadatos de revisión.
    assert doc["tablas"][0]["tabla"] == "Tabla 1"
    assert set(doc["tablas"][0]) == {
        "tabla", "articulo", "descripcion", "columnas", "unidad", "filas",
    }


# --- TableDrafter (integración de las piezas, con cliente falso) --------------

def test_transcribe_page_usa_vision_y_devuelve_json():
    client = _FakeClient({"tablas": [_TABLA_OK]})
    drafter = TableDrafter(_settings(), client=client)
    raw = drafter._transcribe_page(b"PNG", "Artículo 10 ... Tabla 1", 0)
    assert raw == {"tablas": [_TABLA_OK]}
    # Se envió un bloque de imagen en base64 y salida estructurada.
    (call,) = client.messages.calls
    contenido = call["messages"][0]["content"]
    assert contenido[0]["type"] == "image"
    assert contenido[0]["source"]["media_type"] == "image/png"
    assert "output_config" in call and call["thinking"] == {"type": "adaptive"}
    # El texto de la página se adjunta como contexto.
    assert "Artículo 10" in contenido[1]["text"]


def test_transcribe_page_sin_texto_devuelve_tablas_vacias():
    client = _FakeClient("")  # el modelo no devuelve texto
    drafter = TableDrafter(_settings(), client=client)
    assert drafter._transcribe_page(b"PNG", "", 0) == {"tablas": []}


def test_get_client_sin_clave_lanza():
    drafter = TableDrafter(_settings())  # sin cliente inyectado ni clave
    with pytest.raises(LLMConfigurationError):
        drafter._get_client()


def test_draft_from_pdf_detecta_paginas_y_transcribe(monkeypatch):
    textos = [
        "Portada",
        "Artículo 10. Ver la Tabla 1.",
        "Sin tablas",
    ]
    monkeypatch.setattr(
        "app.services.table_drafter._extract_page_texts", lambda pdf: textos
    )
    client = _FakeClient({"tablas": [_TABLA_OK]})
    drafter = TableDrafter(
        _settings(),
        client=client,
        render_page=lambda pdf, i, scale: b"PNG",
    )
    resultado = drafter.draft_from_pdf(b"%PDF-fake")
    assert resultado.paginas == [2]  # solo la página con la Tabla 1
    assert len(resultado.borradores) == 1


def test_draft_from_pdf_respeta_paginas_forzadas(monkeypatch):
    textos = ["p0", "p1", "p2", "p3"]
    monkeypatch.setattr(
        "app.services.table_drafter._extract_page_texts", lambda pdf: textos
    )
    render_calls: list[int] = []

    def fake_render(pdf, i, scale):
        render_calls.append(i)
        return b"PNG"

    client = _FakeClient({"tablas": [_TABLA_OK]})
    drafter = TableDrafter(_settings(), client=client, render_page=fake_render)
    # Fuera de rango (99) se ignora; 3 -> índice 2.
    resultado = drafter.draft_from_pdf(b"%PDF", paginas=[3, 99])
    assert render_calls == [2]
    assert resultado.paginas == [3]
