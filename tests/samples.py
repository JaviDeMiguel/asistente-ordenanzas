"""Textos de ejemplo para los tests.

`SAMPLE_ORDENANZA` imita el formato del BOP: una línea de cabecera del boletín
(ruido a limpiar), estructura Título/Capítulo, artículos numerados de forma
secuencial y una referencia en mayúscula a un «Artículo 20» dentro del cuerpo
del artículo 4, para verificar que el troceador no la confunde con un
encabezado real.
"""

SAMPLE_ORDENANZA = """25 Febrero 2013 B.O.P. de Toledo Número 45

TÍTULO I.- DISPOSICIONES GENERALES

Artículo 1.- Objeto de la ordenanza.
Esta ordenanza regula la conservación y la inspección técnica de los edificios
del municipio.

Artículo 2.- Ámbito de aplicación.
Se aplica a todos los edificios situados en el término municipal.

CAPÍTULO II.- LA INSPECCIÓN TÉCNICA DE EDIFICIOS

Artículo 3.- Edificios sujetos a inspección.
Los edificios con una antigüedad superior a cincuenta años deberán someterse a
la inspección técnica de edificios.

Artículo 4.- Periodicidad de las inspecciones.
Las sucesivas inspecciones se realizarán cada cinco años.
Según el Artículo 20 de la normativa autonómica se estará a lo dispuesto en la
legislación aplicable.

Artículo 5.- Régimen sancionador.
La falta de presentación del certificado se sanciona con multa de 600 a 6.000
euros.

Disposición final
Esta ordenanza entrará en vigor al día siguiente de su publicación.
"""
