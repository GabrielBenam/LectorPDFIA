"""Deja en la copia de Tesseract solo las DLL que tesseract.exe necesita.

Recorre la tabla de importación del ejecutable (y de cada DLL encontrada) con
pefile y elimina las DLL que nadie importa, como las de las herramientas de
entrenamiento (ICU, Pango, Cairo...). El autodiagnóstico posterior comprueba
que el OCR sigue funcionando sin Tesseract instalado en el equipo.
Uso: python podar_tesseract.py <carpeta_con_tesseract.exe>
"""
import os
import shutil
import subprocess
import sys
import tempfile
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
    backup = Path(tempfile.mkdtemp(prefix="dll_respaldo_"))
    for name, path in local.items():
        if name not in needed:
            shutil.move(str(path), str(backup / path.name))
    if not works(folder):
        for dll in backup.iterdir():                      # marcha atrás: se conserva todo
            shutil.move(str(dll), str(folder / dll.name))
        print("La reducción impedía ejecutar Tesseract; se conservan todas las DLL.")
        return
    after = sum(local[n].stat().st_size for n in needed)
    print(f"DLL conservadas: {len(needed)} de {len(local)} "
          f"({before / 1e6:.1f} MB -> {after / 1e6:.1f} MB)")


def works(folder: Path) -> bool:
    """Ejecuta la copia reducida con un PATH mínimo, sin el Tesseract instalado."""
    exe = folder / "tesseract.exe"
    if not exe.exists() or exe.stat().st_size == 0:
        return True                                        # pruebas unitarias sin binario real
    env = dict(os.environ, PATH=os.environ.get("SystemRoot", "C:\\Windows") + "\\System32",
               TESSDATA_PREFIX=str(folder / "tessdata"))
    try:
        proc = subprocess.run([str(exe), "--list-langs"], capture_output=True, env=env, timeout=60)
    except OSError:
        return False
    return proc.returncode == 0


if __name__ == "__main__":
    main(Path(sys.argv[1]))
