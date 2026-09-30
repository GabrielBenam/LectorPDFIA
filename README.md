# Lector PDF para IA — versión 0.3.0

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
