* Encoding: UTF-8.
* ============================================================================
* Plantilla: procesar EN LOTE todos los .sav de una carpeta usando el Python
* que SPSS lleva integrado (no hay que instalar nada: se ejecuta dentro de
* SPSS, desde una ventana de sintaxis normal).
*
* Para cada .sav de la carpeta genera un archivo de resultados .spv con los
* descriptivos y las frecuencias. Cambia la variable `carpeta` y ejecuta.
* ============================================================================.

BEGIN PROGRAM PYTHON3.
import glob
import os

import spss

carpeta = r"C:\proyectos\encuesta\datos"

archivos = sorted(glob.glob(os.path.join(carpeta, "*.sav")))
print("Archivos a procesar:", len(archivos))

for sav in archivos:
    nombre = os.path.splitext(os.path.basename(sav))[0]
    salida = os.path.join(carpeta, "informe_" + nombre + ".spv")
    print("Procesando", sav)
    spss.Submit("""
GET FILE='{sav}'.
DATASET NAME actual WINDOW=FRONT.
DESCRIPTIVES VARIABLES=ALL /STATISTICS=MEAN STDDEV MIN MAX.
FREQUENCIES VARIABLES=ALL /ORDER=ANALYSIS.
OUTPUT SAVE OUTFILE='{salida}'.
OUTPUT CLOSE *.
DATASET CLOSE actual.
""".format(sav=sav, salida=salida))

print("Terminado.")
END PROGRAM.
