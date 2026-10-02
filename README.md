# Lector PDF para IA — versión 0.5.0

Convierte libros PDF en texto Markdown compacto para consultarlos con Claude, Gemini, ChatGPT o NotebookLM. Todo se procesa en el equipo, sin conexión ni claves de API.

## Qué produce

Cada libro se exporta en su propia carpeta dentro de la carpeta de salida elegida:

    Libros para IA/
      <Libro>/
        <Libro> - COMPLETO.md        texto completo (archivo principal)
        <Libro>.pdf                  copia del PDF original
        <Libro> - figuras 001.pdf    anexo con todas las imágenes, para adjuntar a la IA
        imagenes/Imagen 0001 (<Libro>).png
        partes/<Libro> - parte 001.md

El archivo COMPLETO empieza con un resumen y un índice de títulos con su página. Las ecuaciones de display de PDF digitales se convierten a LaTeX (`$$...$$`, con `\tag{}` para su número); cada resultado se valida contra los caracteres del PDF y, si no coincide, se adjunta la imagen de la ecuación con la marca `[verificar fórmula]`. Superíndices y subíndices se marcan como `^{}` y `_{}`. `[p. N]` marca el inicio de la página N del PDF. Las tablas con líneas se transcriben como tablas Markdown y además se conservan como imagen. Los diagramas se guardan como imagen con su texto resumido en una línea ("Texto en la figura: …"). Se eliminan encabezados y pies repetidos, folios, capas de texto duplicadas y cortes de línea dentro de los párrafos. Las páginas escaneadas pasan por OCR (español e inglés) y se conservan íntegras como imagen.

## Tiempo y cancelación

La columna Tiempo muestra el tiempo activo de cada libro (las pausas no cuentan). Si a los 30 minutos el avance no supera el 10 %, el trabajo se cancela solo. Un PDF de más de 20 páginas en el que al menos el 80 % de una muestra de páginas no tiene texto se considera escaneado y no se convierte.

## Fórmulas

Solo se envían al modelo de LaTeX las fórmulas con estructura bidimensional (fracciones, raíces, sumatorias o integrales con límites, delimitadores grandes) o cuyo texto sale ilegible del PDF. Las fórmulas que ya son Unicode legible en un renglón quedan como texto con ^{} y _{}. Si el LaTeX no se valida y el PDF tiene texto legible, se conserva ese texto con la imagen de la ecuación marcada `[verificar fórmula]`. Las imágenes de ecuaciones quedan en `imagenes`; el anexo PDF contiene solo figuras.

## Diseño de página

Los recuadros sombreados o enmarcados (ejemplos, derivaciones, definiciones) se procesan como texto con sus fórmulas y tablas, no como imagen. Las páginas a dos columnas se leen columna por columna. Los títulos numerados (2.1.1 …) y los títulos de dos renglones se marcan como títulos; las viñetas dibujadas se convierten en "- ". El texto de las figuras raster se lee por OCR ("Texto en la figura (OCR)"). En el texto las imágenes se citan como `Imagen N`; el nombre del libro va en el encabezado y en el nombre de cada archivo.

## Uso con IA

Para libros largos, carga el archivo COMPLETO en un Proyecto de Claude o en NotebookLM, que buscan dentro de documentos grandes. En un chat normal, adjunta solo las partes que necesites. Cuando la pregunta dependa de una gráfica, adjunta también el anexo de figuras.

## Compilar

Sube los archivos a GitHub y ejecuta la acción "Compilar Windows", o en Windows ejecuta `powershell -ExecutionPolicy Bypass -File .\build_windows.ps1` con Python 3.11+, Tesseract 5 e Inno Setup 6 instalados. El resultado queda en `entregables`.

## Autodiagnóstico

`LectorPDFIA.exe --selftest --gui --report informe.json` comprueba el Tesseract incluido, los idiomas, la conversión de un PDF digital y uno escaneado, los archivos exportados y la ventana. Código de salida 0 = correcto.

## Línea de comandos

`LectorPDFIA.exe --cli "Libro.pdf" --output "C:\LibrosIA" --lang spa+eng --max-pages 200`

## Límites

No resume ni reescribe el contenido. La conversión a LaTeX usa un modelo local (LaTeX-OCR/pix2tex, licencia MIT) y solo aplica a PDF digitales; en páginas escaneadas las fórmulas quedan en la imagen de la página. Ecuaciones matriciales, sistemas de varias líneas y fórmulas muy largas pueden requerir verificación. La calidad del OCR depende del escaneo. Tablas sin líneas, texto en varias columnas y gráficas vectoriales pequeñas pueden extraerse de forma imperfecta. No abre PDF protegidos con contraseña. PyMuPDF usa licencia AGPL: si distribuyes el programa a terceros, publica el código fuente o adquiere una licencia comercial.
