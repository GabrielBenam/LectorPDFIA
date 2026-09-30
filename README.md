# Lector PDF para IA — versión 0.1.1

Convierte libros PDF en texto por partes y anexos visuales. Todo se exporta en **una sola carpeta**. Cada figura lleva el identificador `Imagen N (Nombre del PDF)`; su archivo tiene además un prefijo único para evitar colisiones entre dos PDFs homónimos de distintas carpetas.

## Qué produce

Para `Gudmundsson.pdf`, crea `Gudmundsson__<id>__INDICE.md`, partes `Gudmundsson__<id>__parte_001.md`, imágenes `<id>__Imagen 0001 (Gudmundsson).png` y anexos `Gudmundsson__<id>__figuras_001.pdf`. Los enlaces entre esos archivos funcionan dentro de la misma carpeta. Cada imagen está identificada en el texto y conserva el número de página. Una página escaneada se conserva íntegra como imagen, además de aplicar OCR.

**Uso con Claude:** sube primero el índice y las partes de texto que necesites. Cuando necesites analizar gráficas, adjunta el anexo visual de ese libro. El enlace a un PNG en el Markdown no hace que Claude reciba el PNG automáticamente. El anexo se divide por un máximo de 60 imágenes y un límite aproximado de 19 MB de datos de imagen por archivo; si excede los límites de tamaño de tu servicio, sube imágenes específicas.

La extracción de fórmulas, tablas y orden de lectura depende de la calidad del PDF. El OCR no interpreta con garantías las ecuaciones; el anexo visual permite verificarlas. La detección de dibujos vectoriales es aproximada y puede incluir tablas o pasar por alto gráficas pequeñas. El programa no resume ni inventa contenido.
Las páginas con símbolos matemáticos reconocibles se conservan íntegras en el anexo para cotejar la transcripción. Las fórmulas no se convierten automáticamente a LaTeX en esta versión.

## Crear el instalador y la versión portable

Opción sin instalar nada en tu equipo: sube este proyecto a un repositorio de GitHub y ejecuta la acción "Compilar Windows" (archivo `.github/workflows/windows.yml`). Compila en Windows x64 real, prueba el ejecutable, el instalador instalado en silencio y la versión portable después de desinstalar Tesseract del equipo, y publica `LectorPDFIA_Instalador.exe`, `LectorPDFIA_Portable.zip` y los informes `autodiagnostico_*.json` como artefacto descargable.

Opción en un Windows propio: instala Python 3.11+ x64, Tesseract OCR 5 x64 e Inno Setup 6, y ejecuta `powershell -ExecutionPolicy Bypass -File .\build_windows.ps1`. Los resultados quedan en `entregables`.

## Autodiagnóstico

`LectorPDFIA.exe --selftest --gui --report informe.json` comprueba que usa el Tesseract incluido, que existen los idiomas español e inglés, convierte un PDF digital y uno escaneado, abre cada archivo exportado y abre la ventana. Código de salida 0 = correcto.

## Línea de comandos

`python -m lector_pdf_ia --cli "Libro.pdf" --output "C:\LibrosIA" --lang spa+eng --max-pages 40`

## Organización y recuperación

Los archivos finales quedan planos en la carpeta elegida. La base de datos y los checkpoints internos viven en la carpeta de datos del usuario, no en la exportación. Cada página se guarda al terminarse; al completar un trabajo se crean las partes e índice. Una segunda conversión del mismo PDF reemplaza sus archivos con el mismo nombre. Si modificas el PDF de origen o cambias la carpeta o idiomas, se invalida el checkpoint y se inicia de nuevo.
