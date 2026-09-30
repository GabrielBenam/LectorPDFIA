"""Deja en la copia de Tesseract solo las DLL que tesseract.exe necesita.

Recorre la tabla de importación del ejecutable (y de cada DLL encontrada) con
pefile y elimina las DLL que nadie importa, como las de las herramientas de
entrenamiento (ICU, Pango, Cairo...). El autodiagnóstico posterior comprueba
que el OCR sigue funcionando sin Tesseract instalado en el equipo.
Uso: python podar_tesseract.py <carpeta_con_tesseract.exe>
"""
import sys
from pathlib import Path

import pefile


def imports(path: Path) -> set[str]:
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = set()
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for entry in getattr(pe, attr, []):
            names.add(entry.dll.decode("ascii", "ignore").lower())
    pe.close()
    return names


def main(folder: Path) -> None:
    local = {p.name.lower(): p for p in folder.glob("*.dll")}
    needed, pending = set(), [folder / "tesseract.exe"]
    while pending:
        for name in imports(pending.pop()):
            if name in local and name not in needed:
                needed.add(name)
                pending.append(local[name])
    before = sum(p.stat().st_size for p in local.values())
    for name, path in local.items():
        if name not in needed:
            path.unlink()
    after = sum(local[n].stat().st_size for n in needed)
    print(f"DLL conservadas: {len(needed)} de {len(local)} "
          f"({before / 1e6:.1f} MB -> {after / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
