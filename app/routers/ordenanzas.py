"""Rutas para dar de alta, listar, consultar y eliminar ordenanzas."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.models.schemas import (
    ErrorResponse,
    OrdinanceIngestRequest,
    OrdinanceSummary,
)
from app.repositories.ordinance_repository import (
    OrdinanceRecord,
    OrdinanceRepository,
    get_ordinance_repository,
)
from app.services.ingest_service import (
    EmptyOrdinanceError,
    IngestService,
    get_ingest_service,
)

router = APIRouter(prefix="/ordenanzas", tags=["ordenanzas"])


def _to_summary(record: OrdinanceRecord) -> OrdinanceSummary:
    """Convierte el registro almacenado en su representación pública."""
    return OrdinanceSummary(
        id=record.id,
        titulo=record.titulo,
        fuente=record.fuente,
        articulo_count=record.articulo_count,
        chunk_count=record.chunk_count,
        char_count=record.char_count,
        created_at=record.created_at,
    )


@router.post(
    "",
    response_model=OrdinanceSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta una ordenanza a partir de texto plano",
)
def create_ordinance(
    payload: OrdinanceIngestRequest,
    service: IngestService = Depends(get_ingest_service),
) -> OrdinanceSummary:
    try:
        record = service.ingest_text(
            titulo=payload.titulo,
            contenido=payload.contenido,
            fuente=payload.fuente,
        )
    except EmptyOrdinanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _to_summary(record)


@router.post(
    "/upload",
    response_model=OrdinanceSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta una ordenanza a partir de un archivo PDF",
)
async def upload_ordinance(
    titulo: str = Form(..., min_length=3, max_length=300),
    file: UploadFile = File(..., description="Archivo PDF de la ordenanza."),
    fuente: str | None = Form(default=None, max_length=300),
    service: IngestService = Depends(get_ingest_service),
) -> OrdinanceSummary:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se aceptan archivos PDF.",
        )

    pdf_bytes = await file.read()
    try:
        record = service.ingest_pdf(titulo=titulo, pdf_bytes=pdf_bytes, fuente=fuente)
    except EmptyOrdinanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se pudo extraer texto del PDF.",
        ) from exc
    except Exception as exc:  # p. ej. PDF corrupto
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo procesar el PDF.",
        ) from exc
    return _to_summary(record)


@router.get(
    "",
    response_model=list[OrdinanceSummary],
    summary="Listar las ordenanzas indexadas",
)
def list_ordinances(
    repository: OrdinanceRepository = Depends(get_ordinance_repository),
) -> list[OrdinanceSummary]:
    return [_to_summary(record) for record in repository.list_all()]


@router.get(
    "/{ordenanza_id}",
    response_model=OrdinanceSummary,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
    summary="Obtener los metadatos de una ordenanza",
)
def get_ordinance(
    ordenanza_id: str,
    repository: OrdinanceRepository = Depends(get_ordinance_repository),
) -> OrdinanceSummary:
    record = repository.get(ordenanza_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ordenanza no encontrada.",
        )
    return _to_summary(record)


@router.delete(
    "/{ordenanza_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
    summary="Eliminar una ordenanza",
)
def delete_ordinance(
    ordenanza_id: str,
    service: IngestService = Depends(get_ingest_service),
) -> None:
    if not service.delete_ordinance(ordenanza_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ordenanza no encontrada.",
        )
