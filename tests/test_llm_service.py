"""Tests del servicio LLM (sin tocar la red)."""

import pytest

from app.config import Settings
from app.services.llm_service import LLMConfigurationError, LLMService


def test_ensure_configured_sin_clave_lanza_error():
    service = LLMService(Settings(anthropic_api_key=None))
    with pytest.raises(LLMConfigurationError):
        service.ensure_configured()


def test_build_user_message_incluye_pregunta_y_contexto():
    mensaje = LLMService._build_user_message(
        "¿cada cuánto?",
        ["[Ordenanza X · Artículo 4]\nCada cinco años."],
    )
    assert "¿cada cuánto?" in mensaje
    assert "Artículo 4" in mensaje
    assert "Cada cinco años." in mensaje


class _Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class _FakeResponse:
    content = [_Block("thinking"), _Block("text", "Cada cinco años (art. 4).")]


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse()


class _FakeAnthropic:
    def __init__(self):
        self.messages = _FakeMessages()


def test_answer_se_queda_con_los_bloques_de_texto():
    service = LLMService(Settings(anthropic_api_key="clave"))
    service._client = _FakeAnthropic()  # inyectamos el doble del SDK
    respuesta = service.answer("¿cada cuánto?", ["[Art. 4] Cada cinco años."])
    assert respuesta == "Cada cinco años (art. 4)."
