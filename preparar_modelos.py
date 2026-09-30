"""Descarga el modelo LaTeX-OCR (pix2tex, licencia MIT; exportación ONNX de RapidLaTeXOCR,
Apache-2.0) y lo cuantiza a 8 bits: misma precisión en las pruebas, la mitad de tiempo y
unos 90 MB en lugar de 140 MB. El redimensionador no se usa (no mejoraba la precisión).
Uso: python preparar_modelos.py <carpeta_destino>
"""
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

URL = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/"
FILES = {"encoder.onnx": 80_000_000, "decoder.onnx": 40_000_000, "tokenizer.json": 20_000}


def main(dest: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, minimum in FILES.items():
            target = tmp / name
            print(f"Descargando {name}")
            urllib.request.urlretrieve(URL + name, target)
            if target.stat().st_size < minimum:
                raise SystemExit(f"{name} incompleto ({target.stat().st_size} bytes)")
        for name in ("encoder.onnx", "decoder.onnx"):
            quantize_dynamic(str(tmp / name), str(dest / name), weight_type=QuantType.QUInt8)
        shutil.copy(tmp / "tokenizer.json", dest / "tokenizer.json")
    for p in sorted(dest.iterdir()):
        print(f"{p.name}: {p.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
