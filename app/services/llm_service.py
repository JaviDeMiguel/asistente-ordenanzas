"""Servicio de acceso al LLM (Claude) mediante el SDK oficial de Anthropic.

Encapsula toda la interacción con la API de Anthropic para que el resto de la
aplicación no dependa directamente del SDK. Usamos pensamiento adaptativo
(`thinking: adaptive`), el modo recomendado para los modelos Claude 4.6+.

El prompt de sistema fija el comportamiento del asistente jurídico: responder
solo con los artículos recuperados y **citar siempre el número de artículo y la
ordenanza**, para que la respuesta sea verificable.

Los fallos del proveedor (caído, timeout, error HTTP, respuesta vacía) se
traducen a `LLMProviderError`, que el router convierte en un 502.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import anthropic

from app.config import Settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Eres un asistente que responde preguntas sobre ordenanzas municipales. "
    "Responde de forma precisa y concisa usando EXCLUSIVAMENTE la información de "
    "los artículos de contexto proporcionados. CITA SIEMPRE el número de "
    "artículo y la ordenanza en los que te basas (por ejemplo: «según el "
    "artículo 23 de la Ordenanza de ITE…»). Si la respuesta no está en los "
    "artículos proporcionados, indícalo claramente diciendo que las ordenanzas "
    "facilitadas no lo regulan. No inventes datos ni uses conocimiento externo. "
    "Esta respuesta es orientativa y no constituye asesoramiento jurídico."
)


class LLMConfigurationError(RuntimeError):
    """Se lanza cuando falta la configuración necesaria para llamar al LLM."""


class LLMProviderError(RuntimeError):
    """Se lanza cuando el proveedor del LLM falla o no devuelve una respuesta útil.

    El mensaje es apto para mostrarlo al cliente: no incluye detalles internos
    del SDK (esos se registran en el log).
    """


@contextmanager
def _provider_errors() -> Iterator[None]:
    """Traduce las excepciones del SDK de Anthropic a `LLMProviderError`.

    Cuando llegan aquí, el SDK ya ha agotado sus reintentos automáticos
    (429, 5xx y errores de red).
    """
    try:
        yield
    except anthropic.APITimeoutError as exc:
        logger.warning("El LLM no respondió a tiempo: %s", exc)
        raise LLMProviderError("El proveedor del LLM no respondió a tiempo.") from exc
    except anthropic.APIConnectionError as exc:
        logger.warning("No se pudo conectar con el LLM: %s", exc)
        raise LLMProviderError("No se pudo conectar con el proveedor del LLM.") from exc
    except anthropic.APIError as exc:
        logger.warning("El LLM devolvió un error: %r", exc)
        raise LLMProviderError("El proveedor del LLM devolvió un error.") from exc


class LLMService:
    """Genera respuestas en lenguaje natural a partir de artículos de contexto."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        """Crea (perezosamente) el cliente de Anthropic."""
        if self._client is None:
            if not self._settings.anthropic_api_key:
                raise LLMConfigurationError(
                    "Falta ANTHROPIC_API_KEY. Configúrala en el archivo .env o "
                    "como variable de entorno para poder responder preguntas."
                )
            self._client = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key,
                timeout=self._settings.anthropic_timeout_seconds,
                max_retries=self._settings.anthropic_max_retries,
            )
        return self._client

    def ensure_configured(self) -> None:
        """Verifica que el LLM está configurado (lanza `LLMConfigurationError`)."""
        self._get_client()

    @staticmethod
    def _build_user_message(pregunta: str, contexto: list[str]) -> str:
        """Compone el mensaje de usuario con los artículos y la pregunta."""
        articulos = "\n\n".join(contexto)
        return (
            f"Artículos de las ordenanzas (contexto):\n\n{articulos}\n\n"
            f"Pregunta: {pregunta}"
        )

    def answer(self, pregunta: str, contexto: list[str]) -> str:
        """Pide a Claude una respuesta a `pregunta` usando los `contexto`.

        Args:
            pregunta: Pregunta del usuario.
            contexto: Bloques de texto de los artículos recuperados, ya
                etiquetados con su ordenanza y número de artículo.

        Returns:
            El texto de la respuesta generada por el modelo.

        Raises:
            LLMProviderError: si el proveedor falla o la respuesta no tiene texto.
        """
        client = self._get_client()
        with _provider_errors():
            response = client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.max_answer_tokens,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": self._build_user_message(pregunta, contexto),
                    }
                ],
            )
        # La respuesta puede incluir bloques de pensamiento antes del texto; nos
        # quedamos únicamente con los bloques de tipo "text".
        texto = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not texto:
            logger.warning(
                "El LLM devolvió una respuesta sin texto (stop_reason=%s).",
                response.stop_reason,
            )
            raise LLMProviderError("El proveedor del LLM devolvió una respuesta vacía.")
        return texto
