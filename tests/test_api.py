"""Tests de integración de la API (ingesta, búsqueda y consulta RAG con citas)."""

from tests.samples import SAMPLE_ORDENANZA


def _crear(client, titulo="Ordenanza de ITE", contenido=SAMPLE_ORDENANZA, fuente=None):
    payload = {"titulo": titulo, "contenido": contenido}
    if fuente is not None:
        payload["fuente"] = fuente
    resp = client.post("/ordenanzas", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_alta_lista_y_detalle(client):
    creada = _crear(client, fuente="BOP Toledo nº 45")
    assert creada["articulo_count"] == 5
    assert creada["chunk_count"] == 5
    assert creada["fuente"] == "BOP Toledo nº 45"

    listado = client.get("/ordenanzas").json()
    assert [o["id"] for o in listado] == [creada["id"]]

    detalle = client.get(f"/ordenanzas/{creada['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["titulo"] == "Ordenanza de ITE"

    assert client.get("/ordenanzas/inexistente").status_code == 404


def test_buscar_devuelve_citas_con_metadatos_de_articulo(client):
    ide = _crear(client)["id"]
    resp = client.post(
        "/consultas/buscar",
        json={"pregunta": "cada cuánto se pasa la inspección técnica de edificios"},
    )
    assert resp.status_code == 200
    resultados = resp.json()["resultados"]
    assert resultados  # hay recuperación
    por_articulo = {c["articulo"]: c for c in resultados}
    assert por_articulo["4"]["epigrafe"] == "Periodicidad de las inspecciones"
    assert por_articulo["4"]["capitulo"].startswith("Capítulo II")
    assert all(c["ordenanza_id"] == ide for c in resultados)


def test_preguntar_genera_respuesta_y_cita_articulos(client, llm):
    _crear(client)
    resp = client.post(
        "/consultas",
        json={"pregunta": "¿cada cuánto hay que pasar la inspección?"},
    )
    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["respuesta"] == "RESPUESTA-SIMULADA-DEL-LLM"
    assert cuerpo["fuentes"]
    # El contexto que recibe el LLM lleva las etiquetas de artículo para citar.
    contexto = llm.calls[-1]["contexto"]
    assert any("Artículo" in bloque for bloque in contexto)


def test_consulta_filtrada_por_ordenanza(client):
    ite = _crear(client, titulo="Ordenanza de ITE")["id"]
    _crear(
        client,
        titulo="Ordenanza de Tráfico",
        contenido=(
            "Artículo 1.- Objeto.\n"
            "Regula el tráfico y el estacionamiento de vehículos.\n"
            "Artículo 2.- Velocidad.\n"
            "El límite de velocidad es de 40 kilómetros por hora.\n"
        ),
    )
    resp = client.post(
        "/consultas/buscar",
        json={"pregunta": "inspección de edificios", "ordenanza_id": ite},
    )
    assert resp.status_code == 200
    resultados = resp.json()["resultados"]
    assert resultados
    assert all(c["ordenanza_id"] == ite for c in resultados)


def test_consulta_con_ordenanza_inexistente(client):
    _crear(client)
    for ruta in ("/consultas", "/consultas/buscar"):
        resp = client.post(
            ruta, json={"pregunta": "cualquier cosa", "ordenanza_id": "nope"}
        )
        assert resp.status_code == 404


def test_contenido_vacio_devuelve_422(client):
    resp = client.post("/ordenanzas", json={"titulo": "Vacía", "contenido": "   "})
    assert resp.status_code == 422


def test_borrado_elimina_metadatos_y_vectores(client):
    ide = _crear(client)["id"]
    assert client.delete(f"/ordenanzas/{ide}").status_code == 204
    assert client.get(f"/ordenanzas/{ide}").status_code == 404
    assert client.delete(f"/ordenanzas/{ide}").status_code == 404

    resp = client.post("/consultas/buscar", json={"pregunta": "inspección"})
    assert resp.json()["resultados"] == []


def test_upload_pdf(client, monkeypatch):
    class _FakePage:
        def extract_text(self):
            return SAMPLE_ORDENANZA

    class _FakePdfReader:
        def __init__(self, _stream):
            self.pages = [_FakePage()]

    monkeypatch.setattr("app.services.ingest_service.PdfReader", _FakePdfReader)
    resp = client.post(
        "/ordenanzas/upload",
        data={"titulo": "Ordenanza subida en PDF"},
        files={"file": ("ordenanza.pdf", b"%PDF-1.4 contenido", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["articulo_count"] == 5


def test_upload_tipo_no_pdf(client):
    resp = client.post(
        "/ordenanzas/upload",
        data={"titulo": "No es PDF"},
        files={"file": ("x.txt", b"hola", "text/plain")},
    )
    assert resp.status_code == 415


def test_upload_pdf_corrupto(client):
    resp = client.post(
        "/ordenanzas/upload",
        data={"titulo": "PDF corrupto"},
        files={"file": ("x.pdf", b"esto no es un pdf", "application/pdf")},
    )
    assert resp.status_code == 400


def test_preguntar_sin_clave_llm_devuelve_503(client, monkeypatch):
    # Restauramos el LLMService real (sin clave) para probar el camino 503.
    from app.services.llm_service import LLMService as RealLLMService

    _crear(client)
    monkeypatch.setattr("app.services.qa_service.LLMService", RealLLMService)
    resp = client.post(
        "/consultas", json={"pregunta": "¿cada cuánto la inspección?"}
    )
    assert resp.status_code == 503


def test_consulta_sin_resultados_no_llama_al_llm(client, llm):
    # Sin ordenanzas indexadas no hay recuperación: respuesta informativa y
    # ninguna llamada al LLM.
    resp = client.post("/consultas", json={"pregunta": "cualquier cosa"})
    assert resp.status_code == 200
    assert "No se han encontrado" in resp.json()["respuesta"]
    assert llm.calls == []
