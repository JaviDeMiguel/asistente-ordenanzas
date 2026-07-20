"""Tests del troceado estructural por artículo (el corazón de la adaptación)."""

from app.services.ordinance_parser import parse_ordinance
from tests.samples import SAMPLE_ORDENANZA


def _parse(text, max_words=220, overlap=40):
    return parse_ordinance(text, max_words=max_words, overlap=overlap)


def test_trocea_por_articulo_secuencial():
    articulos = _parse(SAMPLE_ORDENANZA)
    numeros = [a.numero for a in articulos]
    # Cinco artículos reales, en orden; ninguno fantasma (el "Artículo 20" del
    # cuerpo es una referencia, no un encabezado).
    assert numeros == ["1", "2", "3", "4", "5"]
    assert "20" not in numeros


def test_referencia_inline_no_parte_el_articulo():
    articulos = {a.numero: a for a in _parse(SAMPLE_ORDENANZA)}
    art4 = articulos["4"]
    assert art4.epigrafe == "Periodicidad de las inspecciones"
    # El cuerpo conserva tanto su contenido como la referencia inline.
    assert "cada cinco años" in art4.texto
    assert "Artículo 20" in art4.texto


def test_contexto_jerarquico():
    articulos = {a.numero: a for a in _parse(SAMPLE_ORDENANZA)}
    assert articulos["1"].titulo.startswith("Título I")
    assert articulos["1"].capitulo == ""
    assert articulos["3"].capitulo.startswith("Capítulo II")
    # El Título se hereda aunque cambie el Capítulo.
    assert articulos["3"].titulo.startswith("Título I")


def test_limpia_cabecera_del_boletin():
    for art in _parse(SAMPLE_ORDENANZA):
        assert "B.O.P." not in art.texto


def test_excluye_disposiciones_finales():
    textos = " ".join(a.texto for a in _parse(SAMPLE_ORDENANZA))
    assert "entrará en vigor" not in textos


def test_articulo_largo_se_divide_en_partes():
    articulos = _parse(SAMPLE_ORDENANZA, max_words=8, overlap=2)
    por_numero: dict[str, list] = {}
    for art in articulos:
        por_numero.setdefault(art.numero, []).append(art)
    # Algún artículo (p. ej. el 4, más largo) debe partirse en varias partes.
    partido = [num for num, partes in por_numero.items() if len(partes) > 1]
    assert partido
    partes = por_numero[partido[0]]
    assert [p.parte for p in partes] == list(range(len(partes)))
    assert all(p.total_partes == len(partes) for p in partes)


def test_sin_articulado_devuelve_vacio():
    assert _parse("Un texto cualquiera sin estructura de articulado.") == []
