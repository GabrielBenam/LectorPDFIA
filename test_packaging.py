"""Comprobaciones de empaquetado y OCR."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lector_pdf_ia import converter
from lector_pdf_ia.converter import convert, find_tesseract, tesseract_languages
from lector_pdf_ia.selftest import make_scanned_pdf, run, verify_outputs

ROOT = Path(__file__).resolve().parents[1]


class PackagingTest(unittest.TestCase):
    def test_bundled_tesseract_has_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "tesseract" / "tesseract.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            env = {k: v for k, v in os.environ.items() if k != "TESSERACT_EXE"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(sys, "_MEIPASS", tmp, create=True):
                found, tessdata = find_tesseract()
            self.assertEqual(Path(found), exe)
            self.assertEqual(Path(tessdata), exe.parent / "tessdata")

    def test_ocr_failure_is_reported_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "Escaneo.pdf"
            make_scanned_pdf(src, "WING")
            with mock.patch.object(converter, "find_tesseract", return_value=(None, None)):
                res = convert(src, tmp / "out", languages="eng", checkpoint_root=tmp / "cp")
            self.assertEqual(res["ocr_failed"], 1)
            md = (tmp / "out" / res["parts"][0]).read_text(encoding="utf-8")
            self.assertIn("[OCR pendiente", md)

    def test_required_languages_available(self):
        self.assertTrue({"spa", "eng"} <= tesseract_languages())

    def test_spanish_ocr_with_accents(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "Libro español.pdf"
            make_scanned_pdf(src, "CAPÍTULO SUSTENTACIÓN")
            res = convert(src, tmp / "out", languages="spa+eng", checkpoint_root=tmp / "cp")
            md = (tmp / "out" / res["parts"][0]).read_text(encoding="utf-8")
            self.assertIn("SUSTENTACIÓN", md)
            self.assertIn("Imagen 1 (Libro español)", md)
            self.assertEqual(verify_outputs(tmp / "out", res), [])

    def test_selftest_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "r.json"
            with mock.patch("sys.stdout", None):
                self.assertEqual(run(report), 0, report.read_text(encoding="utf-8"))

    def test_cli_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "Gudmundsson.pdf"
            from lector_pdf_ia.selftest import make_digital_pdf
            make_digital_pdf(src)
            env = dict(os.environ, LOCALAPPDATA=str(tmp / "appdata"))
            proc = subprocess.run([sys.executable, str(ROOT / "start.py"), "--cli", str(src),
                                   "--output", str(tmp / "out"), "--lang", "eng"],
                                  capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("INDICE", proc.stdout)

    def test_build_recipe_bundles_ocr(self):
        ps1 = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")
        for token in ("spa.traineddata", "eng.traineddata", "--selftest", "start.py", "Compress-Archive"):
            self.assertIn(token, ps1)
        iss = (ROOT / "instalador.iss").read_text(encoding="utf-8")
        self.assertIn("ArchitecturesAllowed=x64compatible", iss)
        self.assertIn("LectorPDFIA_Instalador", iss)


if __name__ == "__main__":
    unittest.main()
