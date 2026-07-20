# Imagen base ligera y estable (Python 3.12 slim).
FROM python:3.12-slim

# Buenas prácticas para Python en contenedores.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/app/data/app.db \
    CHROMA_PATH=/app/data/chroma

WORKDIR /app

# 1) Dependencias primero: aprovecha la caché de capas de Docker
#    (solo se reinstalan si cambia requirements.txt).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 2) Código de la aplicación.
COPY app ./app

# 3) Usuario sin privilegios y directorio de datos persistente.
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Volumen para conservar la BD SQLite y la base vectorial de Chroma entre reinicios.
VOLUME ["/app/data"]

EXPOSE 8000

# Comprobación de salud usando solo la librería estándar (no requiere curl).
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
