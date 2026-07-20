"""Tablas de la Ordenanza de Contaminación Acústica (Talavera de la Reina).

⚠️  IMPORTANTE: las tablas del BOP van incrustadas como imágenes; estos valores
se han transcrito a mano desde el documento y **deben verificarse** contra el
original antes de usarse en producción. Sirven como semilla del corpus de
demostración.

Uso (ingesta):

    import json, urllib.request
    from examples.tablas_acustica import TABLAS_ACUSTICA
    body = {"titulo": "Ordenanza de Contaminación Acústica",
            "contenido": "<texto del articulado>", "tablas": TABLAS_ACUSTICA}
    # POST /ordenanzas con `body`

El formato de cada tabla coincide con el esquema `TableSpec` de la API.
"""

# Columnas de ruido exterior (índices Ld/Le/Ln por periodo).
_COLS_EXTERIOR = [
    "Ld (índice de ruido, periodo día)",
    "Le (índice de ruido, periodo tarde)",
    "Ln (índice de ruido, periodo noche)",
]
# Columnas de niveles de instalaciones (LAeq,5s por periodo).
_COLS_INSTALACIONES = [
    "LAeq,5s (periodo día)",
    "LAeq,5s (periodo noche)",
]

TABLAS_ACUSTICA = [
    {
        "tabla": "Tabla 1",
        "articulo": "10",
        "descripcion": (
            "Objetivos de calidad acústica para ruido ambiental aplicables a "
            "áreas acústicas exteriores existentes"
        ),
        "columnas": _COLS_EXTERIOR,
        "unidad": "dB",
        "filas": [
            {"clave": "Sectores con predominio de suelo de uso residencial",
             "valores": ["65", "65", "55"]},
            {"clave": "Sectores con predominio de suelo de uso industrial",
             "valores": ["75", "75", "65"]},
            {"clave": "Sectores con predominio de suelo de uso recreativo y de espectáculos",
             "valores": ["73", "73", "63"]},
            {"clave": "Sectores con predominio de suelo de uso terciario (oficinas y servicios)",
             "valores": ["70", "70", "65"]},
            {"clave": "Sectores de uso sanitario, docente y cultural",
             "valores": ["60", "60", "50"]},
        ],
    },
    {
        "tabla": "Tabla 5",
        "articulo": "22",
        "descripcion": (
            "Límites de niveles de inmisión de ruido de instalaciones en "
            "interiores"
        ),
        "columnas": _COLS_INSTALACIONES,
        "unidad": "dB",
        "filas": [
            {"clave": "Vivienda o uso residencial — Estancias", "valores": ["42", "32"]},
            {"clave": "Vivienda o uso residencial — Dormitorios", "valores": ["32", "27"]},
            {"clave": "Hospitalario — Zonas de estancia", "valores": ["42", "32"]},
            {"clave": "Hospitalario — Dormitorios", "valores": ["30", "25"]},
            {"clave": "Comercial — Comercio", "valores": ["52", "52"]},
            {"clave": "Hospedaje — Dormitorios", "valores": ["32", "27"]},
            {"clave": "Administrativo y de oficinas — Despachos profesionales",
             "valores": ["37", "37"]},
            {"clave": "Administrativo y de oficinas — Oficinas",
             "valores": ["42", "42"]},
            {"clave": "Educativo o cultural — Aulas", "valores": ["37", "37"]},
            {"clave": "Educativo o cultural — Salas de lectura",
             "valores": ["32", "32"]},
        ],
    },
    {
        "tabla": "Tabla 6",
        "articulo": "22",
        "descripcion": (
            "Límites de niveles de emisión de ruido de instalaciones al exterior"
        ),
        "columnas": _COLS_INSTALACIONES,
        "unidad": "dB",
        "filas": [
            {"clave": "Sectores de uso residencial", "valores": ["52", "42"]},
            {"clave": "Sectores de uso industrial", "valores": ["62", "52"]},
            {"clave": "Sectores de uso recreativo y de espectáculos", "valores": ["60", "50"]},
            {"clave": "Sectores de uso terciario", "valores": ["57", "47"]},
            {"clave": "Sectores de uso sanitario, docente y cultural", "valores": ["50", "40"]},
        ],
    },
]
