# Asistente de Ordenanzas Municipales (RAG legal)

[![CI](https://github.com/JaviDeMiguel/asistente-ordenanzas/actions/workflows/ci.yml/badge.svg)](https://github.com/JaviDeMiguel/asistente-ordenanzas/actions/workflows/ci.yml)

API construida con **FastAPI** que implementa un sistema **RAG** (Retrieval-Augmented
Generation) especializado en **ordenanzas municipales**: subes el texto (o el PDF)
de una ordenanza y preguntas en lenguaje natural. Las respuestas las genera
**Claude** (Anthropic) usando únicamente los artículos recuperados y **citando el
artículo y la ordenanza** en los que se basa.

Es una adaptación de dominio de un RAG genérico: donde un RAG normal trocea el
texto en bloques de N palabras, aquí el texto se trocea **por artículo**, con lo
que la recuperación devuelve el artículo completo y su número y la respuesta se
puede verificar.

## Por qué un RAG específico para ordenanzas

Las ordenanzas tienen una estructura jerárquica fuerte (Título → Capítulo →
Sección → Artículo) y en lo legal la respuesta **sin fuente no vale**. Tres
decisiones diferencian este proyecto de un RAG genérico:

1. **Troceado por artículo, no por palabras.** Un troceador ingenuo de «180
   palabras con solape» corta los artículos por la mitad y separa la norma de su
   número. Aquí se detecta la estructura del articulado y cada artículo se indexa
   entero, con sus metadatos `{ordenanza, título, capítulo, sección, artículo,
   epígrafe}`. Los artículos muy largos se parten en sub-fragmentos que **repiten
   esos metadatos**. Ver [`app/services/ordinance_parser.py`](app/services/ordinance_parser.py).

   Heurística clave: las ordenanzas del BOP numeran los artículos de forma
   **secuencial**. Aprovechamos eso para distinguir los encabezados reales de
   artículo de las **referencias** en el cuerpo del texto (p. ej. «según el
   artículo 76 del T.R.L.O.T.A.U.»): solo se acepta como nuevo artículo el
   encabezado cuyo número es el del anterior más uno.

2. **Citas por artículo.** Cada fragmento recuperado (`Cita`) lleva su ordenanza,
   número de artículo, epígrafe y contexto jerárquico, y el prompt del sistema
   obliga al modelo a citar («según el artículo 23 de la Ordenanza de ITE…»).

3. **Embeddings semánticos opcionales.** Por defecto se usa un proveedor local
   (léxico, sin claves ni red) apto para tests y demos. Para consultas legales
   reales —donde la pregunta rara vez comparte vocabulario con el texto— se
   recomienda **Voyage AI** (`EMBEDDING_PROVIDER=voyage`), semántico y sin tocar
   el resto de la app.

### Tablas (los valores por celda)

Muchas preguntas reales van sobre **tablas** (p. ej. «¿límite de ruido en zona
residencial de noche?»). En las ordenanzas del BOP esas tablas van **incrustadas
como imágenes**: la capa de texto contiene la leyenda («Tabla 1») pero **no los
valores** (`65 65 55` no aparece en el texto), así que ni pypdf ni pdfplumber
pueden extraerlas. Y para un umbral legal, una lectura automática errónea es peor
que no responder.

Por eso las tablas se ingieren de forma **estructurada** (valores verificados,
campo `tablas` del alta) y se **linealizan a una fila autodescriptiva por
registro**, con el periodo y la unidad explícitos y su artículo asociado. Así la
recuperación funciona en lenguaje natural y la respuesta cita «Tabla 1 (Art.
10)». Ejemplo real (proveedor local, sin clave):

```
Pregunta: "¿límite de ruido en zona residencial por la noche?"
→ [tabla] Tabla 1 · Art. 10 : Sectores … uso residencial: Ld (día) 65 dB;
                              Le (tarde) 65 dB; Ln (noche) 55 dB.
```

La transcripción estructurada de las tablas de la ordenanza acústica está en
[`examples/tablas_acustica.py`](examples/tablas_acustica.py) (verifica los
valores contra el original antes de producción).

Transcribirlas a mano es tedioso, así que hay un **drafter asistido por visión**
([`scripts/draft_tables.py`](scripts/draft_tables.py)): selecciona las páginas con
tabla (por la leyenda «Tabla N» y por las imágenes contiguas a ellas), **recorta
cada tabla** de la página con `pypdfium2` (una imagen por tabla, lo que además
deshace las maquetas a dos columnas) y se la pasa a **Claude (visión)** para que
la transcriba a un **borrador** `TableSpec` en JSON. La salida no se indexa: es un
borrador para que un humano **verifique los valores** antes de publicarlos (para un
umbral legal, un número mal leído es peor que no responder; por eso el modelo marca
la `confianza` y se avisa de las celdas dudosas o desalineadas).

Con `--verificar` cada tabla se transcribe **dos veces** y se reconcilian las
lecturas: cualquier celda en la que las dos pasadas discrepen baja a confianza
«baja» y se lista en los avisos (dos lecturas distintas del mismo número son justo
lo que hay que revisar).

```bash
pip install -r requirements-drafter.txt   # pypdfium2 + Pillow (solo para el drafter)
python scripts/draft_tables.py Ordenanza-Acustica.pdf --verificar   # requiere ANTHROPIC_API_KEY
# → Ordenanza-Acustica.tablas.json  (revisa `tablas` y publícalas con el alta)
```

## Características

- **Ingesta** de ordenanzas desde **texto plano** o **PDF** (con limpieza de las
  cabeceras/pies que el BOP repite en cada página).
- **Tablas estructuradas** linealizadas a filas citables y recuperables.
- **Troceado por artículo** con contexto jerárquico y metadatos.
- **Base vectorial** (**ChromaDB**, índice HNSW por coseno) y **SQLite** para los
  metadatos de cada ordenanza.
- **Recuperación** por similitud de embeddings, con filtro opcional por ordenanza.
- **Respuesta citada** generada por Claude, restringida al contexto recuperado
  para reducir alucinaciones.
- **Búsqueda pura** (`/consultas/buscar`) para inspeccionar qué artículos
  recupera el sistema sin gastar cuota del LLM.
- Validación de entradas/salidas con **Pydantic** y documentación automática en
  `/docs`.

> Corpus **público y compartido** (las ordenanzas no son privadas): a diferencia
> de un RAG multiusuario, no hay capa de autenticación. Añadirla es trivial si el
> despliegue lo requiere.

## Arquitectura

Separación en capas (routers → services → repositories):

```
app/
├── main.py                     # Punto de entrada FastAPI (+ lifespan)
├── config.py                   # Configuración (variables de entorno / .env)
├── db.py                       # Conexión SQLite compartida y esquema
├── models/
│   └── schemas.py              # Esquemas Pydantic (validación)
├── routers/
│   ├── ordenanzas.py           # Alta (texto/PDF), listado, detalle, borrado
│   └── consultas.py            # Preguntar (RAG + citas) y buscar
├── services/
│   ├── ordinance_parser.py     # ★ Troceado estructural por artículo
│   ├── text_utils.py           # Limpieza del BOP, tokenización, troceado
│   ├── ingest_service.py       # Orquestación de la ingesta
│   ├── qa_service.py           # Orquestación del RAG (recuperar → responder)
│   ├── embedding_service.py    # Proveedor de embeddings (local / Voyage)
│   ├── table_drafter.py        # Borrador de tablas por visión (Claude)
│   └── llm_service.py          # Claude con prompt citador
└── repositories/
    ├── ordinance_repository.py # Metadatos de ordenanzas (SQLite)
    └── vector_store.py         # Artículos + embeddings + metadatos (Chroma)

scripts/
└── draft_tables.py             # CLI: PDF → borrador de tablas (visión) para revisar
```

## Endpoints

| Método | Ruta                       | Descripción                                    |
|--------|----------------------------|------------------------------------------------|
| POST   | `/ordenanzas`              | Alta de una ordenanza desde texto plano        |
| POST   | `/ordenanzas/upload`       | Alta de una ordenanza desde un PDF             |
| GET    | `/ordenanzas`              | Listar las ordenanzas indexadas                |
| GET    | `/ordenanzas/{id}`         | Metadatos de una ordenanza                     |
| DELETE | `/ordenanzas/{id}`         | Eliminar una ordenanza (metadatos + vectores)  |
| POST   | `/consultas`               | Preguntar en lenguaje natural (RAG con citas)  |
| POST   | `/consultas/buscar`        | Recuperar los artículos relevantes (sin LLM)   |
| GET    | `/health`                  | Comprobación de estado                         |

Ejemplo de respuesta de `/consultas`:

```json
{
  "pregunta": "¿cada cuánto hay que pasar la inspección técnica de edificios?",
  "respuesta": "Cada cinco años, según el artículo 4 de la Ordenanza de ITE…",
  "fuentes": [
    {
      "ordenanza_titulo": "Ordenanza de ITE",
      "articulo": "4",
      "epigrafe": "Periodicidad de las inspecciones",
      "capitulo": "Capítulo II — La inspección técnica de edificios",
      "texto": "Las sucesivas inspecciones se realizarán cada cinco años…",
      "score": 0.82
    }
  ]
}
```

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # y rellena ANTHROPIC_API_KEY para responder preguntas
uvicorn app.main:app --reload
```

Documentación interactiva en `http://localhost:8000/docs`.

Con Docker:

```bash
docker build -t asistente-ordenanzas .
docker run -p 8000:8000 --env-file .env -v $(pwd)/data:/app/data asistente-ordenanzas
```

## Configuración

Todas las opciones se leen de variables de entorno o de `.env` (ver
[`.env.example`](.env.example)). Las más relevantes:

- `ANTHROPIC_API_KEY` — necesaria para generar respuestas (no para ingesta ni
  búsqueda).
- `EMBEDDING_PROVIDER` — `local` (por defecto) o `voyage` (semántico; requiere
  `VOYAGE_API_KEY` y el paquete `voyageai`).
- `ARTICLE_MAX_WORDS` / `ARTICLE_OVERLAP` — umbral para partir artículos largos.
- `TOP_K` — nº de artículos recuperados por pregunta.

## Tests

```bash
ruff check app tests
pytest            # cobertura mínima exigida: 85 %
```

Los tests corren **offline y sin claves**: el LLM se sustituye por un doble que
registra las llamadas y los embeddings usan el proveedor local. La base de datos
y la base vectorial se fuerzan a memoria.

## Limitaciones y hoja de ruta

- **Tablas: extracción automática (visión).** Como van incrustadas como imágenes,
  hay un drafter que **recorta cada tabla** de la página y la transcribe con
  **Claude (visión)** a un borrador `TableSpec` para revisión humana, con una
  **verificación cruzada** opcional (`--verificar`) que marca las celdas en las que
  dos lecturas discrepan ([`scripts/draft_tables.py`](scripts/draft_tables.py)).
  Pendiente: afinar la selección de páginas para PDFs con maquetas muy distintas y
  una salida con la imagen recortada junto al borrador para revisar de un vistazo.
- **Parser heurístico.** El troceado está afinado para el formato del BOP de
  Toledo (numeración secuencial de artículos). Ante un articulado sin estructura
  reconocible, el sistema recurre a un troceado por palabras (degradación
  elegante), pero pierde las citas por artículo.
- **Respuesta orientativa.** El asistente es una ayuda de consulta, no
  asesoramiento jurídico vinculante; por eso siempre cita la fuente para que sea
  verificable.
