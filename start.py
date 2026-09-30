"""Punto de entrada de PyInstaller: interfaz por defecto, --cli y --selftest."""
import sys

from lector_pdf_ia.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
