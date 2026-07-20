"""Servicio de acceso al LLM (Claude) mediante el SDK oficial de Anthropic.

Encapsula toda la interacción con la API de Anthropic para que el resto de la
aplicación no dependa directamente del SDK. Usamos pensamiento adaptativo
(`thinking: adaptive`), el modo recomendado para los modelos Claude 4.6+.

El prompt de sistema fija el comportamiento del asistente jurídico: responder
solo con los artículos recuperados y **citar siempre el número de artículo y la
ordenanza**, para que la respuesta sea verificable.
"""

import anthropic

from app.config import Settings

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
                api_key=self._settings.anthropic_api_key
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
        """
        client = self._get_client()
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
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
