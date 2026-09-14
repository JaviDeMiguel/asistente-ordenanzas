"""Fallos del proveedor del LLM: timeout, excepciones del SDK y respuesta vacía.

Responden a la pregunta "¿y si el proveedor cae?". El cliente de Anthropic se
sustituye por un doble cuyo `messages.create` falla, y se comprueba que:
  - `LLMService` traduce el fallo a `LLMProviderError` (sin filtrar detalles
    internos del SDK), y
  - `/consultas` responde un 502 limpio: JSON con un `detail` legible y nada más.

Las excepciones se construyen con las clases reales del SDK, igual que las
lanzaría el cliente tras agotar sus reintentos.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from app.config import Settings, get_settings
from app.services.llm_service import LLMProviderError, LLMService
from tests.test_api import _crear

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

# Texto interno de las excepciones del SDK: nunca debe llegar al cliente HTTP.
_DETALLE_SDK = "detalle-interno-del-sdk"

_ERROR_PROVEEDOR = "El proveedor del LLM devolvió un error."
_RESPUESTA_VACIA = "El proveedor del LLM devolvió una respuesta vacía."


def _status_error(
    cls: type[anthropic.APIStatusError], status_code: int
) -> anthropic.APIStatusError:
    response = httpx2.Response(status_code, request=_REQUEST)
    return cls(_DETALLE_SDK, response=response, body=None)


# (fábrica de la excepción del SDK, `detail` esperado en la respuesta)
FALLOS_DEL_SDK = [
    pytest.param(
        lambda: anthropic.APITimeoutError(request=_REQUEST),
        "El proveedor del LLM no respondió a tiempo.",
        id="timeout",
    ),
    pytest.param(
        lambda: anthropic.APIConnectionError(message=_DETALLE_SDK, request=_REQUEST),
        "No se pudo conectar con el proveedor del LLM.",
        id="sin-conexion",
    ),
    pytest.param(
        lambda: _status_error(anthropic.InternalServerError, 500),
        _ERROR_PROVEEDOR,
        id="http-500",
    ),
    pytest.param(
        lambda: _status_error(anthropic.OverloadedError, 529),
        _ERROR_PROVEEDOR,
        id="sobrecargado-529",
    ),
    pytest.param(
        lambda: _status_error(anthropic.RateLimitError, 429),
        _ERROR_PROVEEDOR,
        id="rate-limit-429",
    ),
]


def _respuesta_sin_texto() -> SimpleNamespace:
    """Respuesta del SDK que solo trae pensamiento (p. ej. cortada por max_tokens)."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", text="")],
        stop_reason="max_tokens",
    )


def _servicio(fake_client: MagicMock) -> LLMService:
    service = LLMService(Settings(anthropic_api_key="clave-falsa"))
    service._client = fake_client  # inyectamos el doble del SDK
    return service


@pytest.fixture
def anthropic_falso(client, monkeypatch) -> MagicMock:
    """Usa el `LLMService` real (no `RecordingLLM`) con un cliente de Anthropic falso."""
    fake_client = MagicMock()
    monkeypatch.setattr(
        "app.services.llm_service.anthropic.Anthropic",
        MagicMock(return_value=fake_client),
    )
    settings = get_settings().model_copy(update={"anthropic_api_key": "clave-falsa"})
    monkeypatch.setattr(
        "app.services.qa_service.LLMService",
        lambda _settings: LLMService(settings),
    )
    return fake_client


# --- LLMService ---------------------------------------------------------------


@pytest.mark.parametrize(("fallo", "detalle"), FALLOS_DEL_SDK)
def test_answer_traduce_los_fallos_del_sdk(fallo, detalle):
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = fallo()

    with pytest.raises(LLMProviderError) as info:
        _servicio(fake_client).answer("¿cada cuánto?", ["[Art. 4] Cada cinco años."])

    assert str(info.value) == detalle
    # La excepción del SDK se conserva como causa para depurar.
    assert isinstance(info.value.__cause__, anthropic.APIError)


def test_answer_con_respuesta_sin_texto_lanza_error():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _respuesta_sin_texto()

    with pytest.raises(LLMProviderError, match=_RESPUESTA_VACIA):
        _servicio(fake_client).answer("¿cada cuánto?", ["[Art. 4] Cada cinco años."])


def test_cliente_usa_timeout_y_reintentos_de_la_configuracion(monkeypatch):
    constructor = MagicMock()
    monkeypatch.setattr("app.services.llm_service.anthropic.Anthropic", constructor)
    settings = Settings(
        anthropic_api_key="clave-falsa",
        anthropic_timeout_seconds=12.5,
        anthropic_max_retries=1,
    )

    LLMService(settings).ensure_configured()

    kwargs = constructor.call_args.kwargs
    assert kwargs["timeout"] == 12.5
    assert kwargs["max_retries"] == 1


# --- API ----------------------------------------------------------------------


@pytest.mark.parametrize(("fallo", "detalle"), FALLOS_DEL_SDK)
def test_preguntar_si_el_proveedor_falla_devuelve_502_limpio(
    client, anthropic_falso, fallo, detalle
):
    anthropic_falso.messages.create.side_effect = fallo()
    _crear(client)

    resp = client.post(
        "/consultas", json={"pregunta": "¿cada cuánto hay que pasar la inspección?"}
    )

    assert resp.status_code == 502
    assert resp.headers["content-type"] == "application/json"
    assert resp.json() == {"detail": detalle}
    assert _DETALLE_SDK not in resp.text
    anthropic_falso.messages.create.assert_called_once()


def test_preguntar_con_respuesta_vacia_devuelve_502(client, anthropic_falso):
    anthropic_falso.messages.create.return_value = _respuesta_sin_texto()
    _crear(client)

    resp = client.post(
        "/consultas", json={"pregunta": "¿cada cuánto hay que pasar la inspección?"}
    )

    assert resp.status_code == 502
    assert resp.json() == {"detail": _RESPUESTA_VACIA}
