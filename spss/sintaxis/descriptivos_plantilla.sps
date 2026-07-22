* Encoding: UTF-8.
* ============================================================================
* Plantilla: análisis descriptivo básico de un archivo de datos.
*
* Uso: cambia las dos rutas del FILE HANDLE y ejecuta toda la sintaxis
* (Ctrl+A, Ctrl+R). También puede lanzarse desatendida con un trabajo de
* producción (ver README.md, «Vía 1»).
* ============================================================================.

* --- Rutas: lo único que hay que tocar ---.
FILE HANDLE datos     /NAME='C:\proyectos\encuesta\datos.sav'.
FILE HANDLE resultado /NAME='C:\proyectos\encuesta\resultados.xlsx'.

GET FILE='datos'.
DATASET NAME encuesta WINDOW=FRONT.

* --- Descriptivos de todas las variables numéricas ---.
DESCRIPTIVES VARIABLES=ALL
  /STATISTICS=MEAN STDDEV MIN MAX.

* --- Frecuencias (útil para categóricas; acota la lista si hay muchas) ---.
FREQUENCIES VARIABLES=ALL
  /ORDER=ANALYSIS.

* --- Exportar el visor de resultados a Excel ---.
OUTPUT EXPORT
  /CONTENTS EXPORT=VISIBLE
  /XLSX DOCUMENTFILE='resultado'
    OPERATION=CREATEFILE.
