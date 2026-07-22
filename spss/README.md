# Automatización de tareas en IBM SPSS

Kit autocontenido (independiente del resto del repositorio) para automatizar
tareas repetitivas con IBM SPSS Statistics. Hay **tres vías**, de menos a más
programación; se pueden combinar.

## Vía 1 — Sintaxis SPSS (`.sps`): la automatización nativa

Todo lo que se hace con menús en SPSS se puede escribir como **sintaxis** y
reejecutar con otros datos cambiando una ruta. Es la forma más sencilla de no
repetir clics: pega la sintaxis en una ventana de sintaxis y ejecútala
(`Ctrl+A`, `Ctrl+R`). Truco: cualquier diálogo de SPSS tiene el botón
**«Pegar»**, que en lugar de ejecutar escribe la sintaxis equivalente — así se
aprende sin memorizar comandos.

- [`sintaxis/descriptivos_plantilla.sps`](sintaxis/descriptivos_plantilla.sps):
  plantilla de descriptivos + frecuencias que exporta los resultados a Excel.
  Solo hay que cambiar las dos rutas del principio.

Para lanzarla **desatendida** (sin abrir SPSS a mano), usa un **trabajo de
producción**: en SPSS, `Utilidades → Trabajo de producción`, añade el `.sps` y
guarda un `.spj`; después puede ejecutarse desde la línea de comandos de
Windows (ajusta la versión en la ruta):

```bat
"C:\Program Files\IBM\SPSS Statistics\stats.exe" -production silent "C:\proyectos\mi_trabajo.spj"
```

Eso permite programarlo con el Programador de tareas de Windows (por ejemplo,
cada lunes a las 8:00).

## Vía 2 — Python **dentro** de SPSS: bucles y lotes

SPSS Statistics lleva Python integrado: dentro de una sintaxis, un bloque
`BEGIN PROGRAM PYTHON3. … END PROGRAM.` puede hacer bucles, leer carpetas y
enviar sintaxis con `spss.Submit()`. Es la vía natural cuando la tarea es
«repetir este análisis para cada archivo/variable/grupo».

- [`sintaxis/lote_python_dentro_spss.sps`](sintaxis/lote_python_dentro_spss.sps):
  procesa todos los `.sav` de una carpeta y genera un `.spv` de resultados por
  archivo. Cambia la variable `carpeta` y ejecuta.

## Vía 3 — Python **sin** SPSS: `savtools.py`

Para tareas de datos (no de análisis avanzado) ni siquiera hace falta tener
SPSS instalado: la librería `pyreadstat` lee y escribe `.sav` con todos sus
metadatos (etiquetas de variable, etiquetas de valor, niveles de medida).
[`savtools.py`](savtools.py) es un CLI listo para usar:

```bash
pip install -r requirements.txt

# Resumen del archivo: casos, variables, etiquetas, medidas
python savtools.py info encuesta.sav

# Exportar a CSV/Excel, opcionalmente con etiquetas en vez de códigos (1 -> 'Mujer')
python savtools.py exportar encuesta.sav --formato xlsx --etiquetas

# Informe Excel: diccionario de variables + descriptivos + frecuencias
python savtools.py informe encuesta.sav
```

Todos los subcomandos aceptan **varios archivos** a la vez (p. ej.
`python savtools.py informe datos/*.sav`), así que sirven para lotes sin
escribir nada más.

## De la imagen de un cuestionario a la matriz de datos

[`cuestionario_a_matriz.py`](cuestionario_a_matriz.py) automatiza la **grabación
de datos**: recibe fotos o escaneos de cuestionarios y genera la matriz lista
para SPSS. Funciona en dos pasos, con revisión humana entre ambos (la visión
propone, el humano verifica — el mismo enfoque que el drafter de tablas del
repositorio):

```bash
export ANTHROPIC_API_KEY=...        # en Windows: set ANTHROPIC_API_KEY=...

# 1. Libro de códigos desde la imagen del cuestionario (en blanco o cumplimentado):
#    variables, tipos (única/múltiple/numérica/abierta) y opciones codificadas.
python cuestionario_a_matriz.py codebook cuestionario.png
#    → codebook.json  (REVÍSALO: nombres, códigos, opciones)

# 2. Extraer las respuestas de los cuestionarios cumplimentados y montar la matriz:
python cuestionario_a_matriz.py extraer codebook.json fotos/*.jpg --salida datos
#    → datos.sav              matriz con etiquetas de variable y de valor
#    → datos.csv              la misma matriz en texto plano
#    → datos.incidencias.txt  qué revisar a mano
```

Detalles que importan para que la matriz sea fiable:

- **Una fila por cuestionario, una columna por variable**; las preguntas de
  respuesta múltiple se expanden a una columna 0/1 por opción (`p5_1`, `p5_2`…).
- **Nada se corrige en silencio.** Dos marcas en una pregunta de respuesta
  única, un «treinta» donde va un número o un código fuera del codebook quedan
  como **valor perdido** y anotados en `incidencias.txt` con el caso y la
  variable, para cotejarlos contra la imagen.
- **Cuestionarios de varias páginas**: `--paginas-por-caso N` agrupa cada N
  imágenes consecutivas como un mismo cuestionario.
- **`--verificar`** lee cada cuestionario dos veces y señala en incidencias las
  celdas en las que las dos lecturas discrepan (dobla el coste en llamadas).
- Las imágenes se envían a la API de Anthropic; tenlo en cuenta si los
  cuestionarios contienen datos personales.

## ¿Qué vía elegir?

| Tarea | Vía |
|---|---|
| Repetir el mismo análisis cada semana/mes con datos nuevos | 1 (sintaxis + trabajo de producción) |
| Repetir un análisis para muchos archivos o subgrupos | 2 (Python dentro de SPSS) |
| Convertir, revisar o resumir `.sav` sin abrir SPSS (o sin licencia) | 3 (`savtools.py`) |
| Análisis que necesita los procedimientos estadísticos de SPSS | 1 o 2 (requieren SPSS) |
