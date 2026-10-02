# Compila, empaqueta y PRUEBA Lector PDF para IA en Windows 10/11 x64.
# Resultado: entregables\LectorPDFIA_Instalador.exe y entregables\LectorPDFIA_Portable.zip
# Requisitos SOLO en la máquina de compilación: Python 3.11+ (x64), Tesseract OCR 5 (x64)
# en C:\Program Files\Tesseract-OCR e Inno Setup 6. El usuario final no necesita nada.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path build, entregables | Out-Null

# 1) Tesseract mínimo: ejecutable, DLL y solo los idiomas necesarios.
$tesseract = if ($env:TESSERACT_DIR) { $env:TESSERACT_DIR } else { Join-Path $env:ProgramFiles 'Tesseract-OCR' }
if (-not (Test-Path (Join-Path $tesseract 'tesseract.exe'))) {
    throw "No se encontró tesseract.exe en $tesseract. Instala Tesseract OCR 5 de 64 bits."
}
$tmin = Join-Path $PSScriptRoot 'build\tesseract'
if (Test-Path $tmin) { Remove-Item -Recurse -Force $tmin }
New-Item -ItemType Directory -Force -Path (Join-Path $tmin 'tessdata') | Out-Null
Copy-Item (Join-Path $tesseract 'tesseract.exe') $tmin
Copy-Item (Join-Path $tesseract '*.dll') $tmin
foreach ($lang in @('spa', 'eng')) {   # osd no se usa con --psm 3
    $file = Join-Path $tesseract "tessdata\$lang.traineddata"
    $dest = Join-Path $tmin "tessdata\$lang.traineddata"
    if (Test-Path $file) { Copy-Item $file $dest }
    else {
        Write-Host "Descargando $lang.traineddata (no estaba instalado)"
        Invoke-WebRequest "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$lang.traineddata" -OutFile $dest
    }
}
foreach ($lang in @('spa.traineddata', 'eng.traineddata')) {
    if (-not (Test-Path (Join-Path $tmin "tessdata\$lang"))) { throw "Falta $lang" }
}
if (Test-Path (Join-Path $tesseract 'tessdata\configs')) {
    Copy-Item -Recurse (Join-Path $tesseract 'tessdata\configs') (Join-Path $tmin 'tessdata\configs')
}

# 2) Entorno de compilación y pruebas del código con el Tesseract mínimo.
# Python de compilación: el que prepara GitHub (3.12); en un equipo propio, py -3.12.
if ($env:pythonLocation -and (Test-Path (Join-Path $env:pythonLocation 'python.exe'))) {
    & (Join-Path $env:pythonLocation 'python.exe') -m venv .venv
} else {
    py -3.12 -m venv .venv
}
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno de Python 3.12.' }
$env:PYTHONUTF8 = '1'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación de dependencias.' }
& $python podar_tesseract.py $tmin
if ($LASTEXITCODE -ne 0) { throw 'No se pudo reducir la copia de Tesseract.' }
$models = Join-Path $PSScriptRoot 'build\latex_ocr'
& $python preparar_modelos.py $models
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el modelo de fórmulas.' }
# Runtime de Visual C++ junto al programa (onnxruntime lo necesita en equipos sin él).
$vc = @('msvcp140.dll', 'msvcp140_1.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll') |
    ForEach-Object { Join-Path "$env:SystemRoot\System32" $_ } | Where-Object { Test-Path $_ }
$vcArgs = $vc | ForEach-Object { '--add-binary'; "$_;." }
$env:TESSERACT_EXE = Join-Path $tmin 'tesseract.exe'
$env:TESSDATA_PREFIX = Join-Path $tmin 'tessdata'
$env:LECTOR_LATEX_MODELS = $models
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Fallaron las pruebas del código fuente.' }
Remove-Item Env:TESSERACT_EXE, Env:TESSDATA_PREFIX, Env:LECTOR_LATEX_MODELS

# 3) Ejecutable (carpeta onedir, sin consola).
& $python -m PyInstaller --clean --noconfirm --noconsole --onedir --name LectorPDFIA `
    --icon icono.ico --add-data "icono.ico;." --collect-submodules lector_pdf_ia `
    --collect-all tkinterdnd2 --collect-binaries onnxruntime `
    --exclude-module pandas --exclude-module scipy --exclude-module matplotlib `
    --exclude-module fontTools --exclude-module lxml --exclude-module IPython `
    --exclude-module sympy --exclude-module onnx --exclude-module onnxruntime.quantization `
    --exclude-module onnxruntime.transformers --exclude-module onnxruntime.tools `
    --add-data "build\latex_ocr;latex_ocr" --add-data "prueba_formulas.pdf;." @vcArgs `
    --add-data "build\tesseract;tesseract" start.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller falló.' }

# 4) Prueba del ejecutable con un PATH sin Python ni Tesseract.
function Test-App([string]$exe, [string]$report) {
    $saved = $env:PATH
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    Remove-Item Env:TESSERACT_EXE, Env:TESSDATA_PREFIX, Env:LECTOR_LATEX_MODELS -ErrorAction SilentlyContinue
    try {
        $p = Start-Process -FilePath $exe -ArgumentList @('--selftest', '--gui', '--report', "`"$report`"") -Wait -PassThru
    } finally { $env:PATH = $saved }
    Get-Content $report -Encoding UTF8 | Write-Host
    if ($p.ExitCode -ne 0) { throw "El autodiagnóstico falló en $exe (código $($p.ExitCode))." }
}
Test-App (Join-Path $PSScriptRoot 'dist\LectorPDFIA\LectorPDFIA.exe') (Join-Path $PSScriptRoot 'entregables\autodiagnostico_portable.json')

# 5) ZIP portable con toda la carpeta (el .exe no funciona aislado).
$zip = Join-Path $PSScriptRoot 'entregables\LectorPDFIA_Portable.zip'
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $PSScriptRoot 'dist\LectorPDFIA') -DestinationPath $zip -CompressionLevel Optimal
$mb = [math]::Round(((Get-ChildItem -Recurse (Join-Path $PSScriptRoot 'dist\LectorPDFIA') | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Tamaño de la aplicación: $mb MB"

# 6) Instalador + instalación silenciosa + prueba del programa instalado.
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Instala Inno Setup 6 para crear LectorPDFIA_Instalador.exe.' }
& $iscc 'instalador.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup falló.' }
$setup = Join-Path $PSScriptRoot 'entregables\LectorPDFIA_Instalador.exe'
$target = Join-Path $env:TEMP 'LectorPDFIA_prueba_instalacion'
$p = Start-Process $setup -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', "/DIR=`"$target`"") -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "La instalación silenciosa falló (código $($p.ExitCode))." }
Test-App (Join-Path $target 'LectorPDFIA.exe') (Join-Path $PSScriptRoot 'entregables\autodiagnostico_instalado.json')
Start-Process (Join-Path $target 'unins000.exe') -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES') -Wait
Write-Host 'Listo: entregables\LectorPDFIA_Instalador.exe y entregables\LectorPDFIA_Portable.zip'
