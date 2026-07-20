"""Rutas de consulta: preguntar (RAG con citas) y buscar artículos."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.schemas import (
    AnswerResponse,
    ErrorResponse,
    QuestionRequest,
    SearchResponse,
)
from app.services.llm_service import LLMConfigurationError
from app.services.qa_service import (
    OrdinanceNotFoundError,
    QAService,
    get_qa_service,
)

router = APIRouter(prefix="/consultas", tags=["consultas"])


@router.post(
    "",
    response_model=AnswerResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
    summary="Preguntar en lenguaje natural sobre las ordenanzas (RAG con citas)",
)
def preguntar(
    payload: QuestionRequest,
    service: QAService = Depends(get_qa_service),
) -> AnswerResponse:
    try:
        return service.answer(
            pregunta=payload.pregunta,
            ordenanza_id=payload.ordenanza_id,
            top_k=payload.top_k,
        )
    except OrdinanceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ordenanza no encontrada.",
        ) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post(
    "/buscar",
    response_model=SearchResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
    summary="Buscar los artículos más relevantes (sin generar respuesta)",
)
def buscar(
    payload: QuestionRequest,
    service: QAService = Depends(get_qa_service),
) -> SearchResponse:
    try:
        return service.search(
            consulta=payload.pregunta,
            ordenanza_id=payload.ordenanza_id,
            top_k=payload.top_k,
        )
    except OrdinanceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ordenanza no encontrada.",
        ) from exc
