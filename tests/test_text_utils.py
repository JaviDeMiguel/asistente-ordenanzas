"""Tests de las utilidades de texto (limpieza, tokenización, troceado)."""

import pytest

from app.services.text_utils import clean_boletin_text, split_words, tokenize


def test_clean_elimina_cabeceras_del_boletin():
    texto = (
        "25 Febrero 2013 B.O.P. de Toledo Número 45\n"
        "Depósito Legal: TO - 1 - 1958\n"
        "Número 122\n"
        "Artículo 1.- Objeto.\n"
        "Contenido real del artículo.\n"
        "44\n"
    )
    limpio = clean_boletin_text(texto)
    assert "B.O.P." not in limpio
    assert "Depósito Legal" not in limpio
    assert "Número 122" not in limpio
    assert "Contenido real del artículo." in limpio
    # El número de página suelto (44) se elimina.
    assert "\n44" not in limpio


def test_tokenize_filtra_stopwords_numeros_y_cortos():
    tokens = tokenize("El edificio de 50 años y la inspección")
    assert "edificio" in tokens
    assert "inspección" in tokens
    assert "el" not in tokens  # stopword
    assert "50" not in tokens  # número
    assert "y" not in tokens   # longitud 1


def test_split_words_fragmento_unico_si_es_corto():
    assert split_words("una dos tres", max_words=10, overlap=2) == ["una dos tres"]


def test_split_words_trocea_con_solapamiento():
    texto = " ".join(f"p{i}" for i in range(25))
    partes = split_words(texto, max_words=10, overlap=3)
    assert len(partes) > 1
    # Hay solapamiento: el final de una parte reaparece al inicio de la siguiente.
    primera_fin = partes[0].split()[-3:]
    segunda_ini = partes[1].split()[:3]
    assert primera_fin == segunda_ini


def test_split_words_valida_argumentos():
    with pytest.raises(ValueError):
        split_words("x", max_words=0, overlap=0)
    with pytest.raises(ValueError):
        split_words("x", max_words=5, overlap=5)


def test_split_words_texto_vacio():
    assert split_words("   ", max_words=10, overlap=2) == []
