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
from lector_pdf_ia.converter import convert, find_tesseract, fitz, tesseract_languages
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
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            self.assertIn("[OCR pendiente", md)

    def test_required_languages_available(self):
        self.assertTrue({"spa", "eng"} <= tesseract_languages())

    def test_spanish_ocr_with_accents(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "Libro español.pdf"
            make_scanned_pdf(src, "CAPÍTULO SUSTENTACIÓN")
            res = convert(src, tmp / "out", languages="spa+eng", checkpoint_root=tmp / "cp")
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
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
            self.assertIn("COMPLETO", proc.stdout)

    def test_build_recipe_bundles_ocr(self):
        ps1 = (ROOT / "build_windows.ps1").read_text(encoding="utf-8")
        for token in ("spa.traineddata", "eng.traineddata", "--selftest", "start.py", "Compress-Archive",
                      "podar_tesseract.py", "tkinterdnd2", "icono.ico", "preparar_modelos.py",
                      "prueba_formulas.pdf", "latex_ocr"):
            self.assertIn(token, ps1)
        iss = (ROOT / "instalador.iss").read_text(encoding="utf-8")
        self.assertIn("ArchitecturesAllowed=x64compatible", iss)
        self.assertIn("LectorPDFIA_Instalador", iss)


if __name__ == "__main__":
    unittest.main()


class OptionalFeaturesTest(unittest.TestCase):
    def test_prune_script_keeps_import_closure(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("podar", ROOT / "podar_tesseract.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for n in ("tesseract.exe", "a.dll", "b.dll", "icu.dll"):
                (tmp / n).write_bytes(b"")
            graph = {"tesseract.exe": {"a.dll", "kernel32.dll"}, "a.dll": {"b.dll"}, "b.dll": set()}
            with mock.patch.object(mod, "imports", side_effect=lambda p: graph[p.name]):
                mod.main(tmp)
            self.assertEqual(sorted(p.name for p in tmp.iterdir()),
                             ["a.dll", "b.dll", "tesseract.exe"])

    def test_icon_present(self):
        self.assertTrue((ROOT / "icono.ico").is_file())


MODELS = converter._latex_dir()


class FormulaTest(unittest.TestCase):
    @unittest.skipUnless(MODELS, "modelos de fórmulas no disponibles")
    def test_display_formulas_to_latex(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            res = convert(ROOT / "prueba_formulas.pdf", tmp / "out", languages="eng",
                          checkpoint_root=tmp / "cp")
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            squash = md.replace(" ", "").replace("{", "").replace("}", "")
            self.assertEqual(res["equations"], 3)
            self.assertEqual(res["equations_flagged"], 0)
            self.assertIn(r"$$C_L=\frac2L\rhoV^2S\tag3.1$$", squash)
            self.assertIn(r"\sigma=\fracMyI\tag3.2", squash)
            self.assertIn(r"\sum_i=1^nF_i=ma", squash)
            self.assertNotIn("(3.1)", md)                  # el número pasa a \\tag
            self.assertEqual(res["figures"], 0)            # sin imágenes de página completa

    def test_formula_detection_without_model(self):
        with mock.patch.dict(converter._ENGINE, {"ocr": None, "error": "prueba"}):
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                res = convert(ROOT / "prueba_formulas.pdf", tmp / "out", languages="eng",
                              checkpoint_root=tmp / "cp")
                md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
                self.assertEqual(res["equations_flagged"], 3)
                self.assertIn("Ecuación 1 (prueba_formulas)", md)
                self.assertIn("[fórmula sin convertir]", md)

    def test_symbol_font_and_text_markup(self):
        from lector_pdf_ia.converter import _clean_text, _fix_symbol, _unreliable
        self.assertEqual(_fix_symbol("\uf0b7 \uf061\uf0a3\uf0ae\uf0be", "SymbolMT"), "• α≤→—")
        self.assertFalse(_unreliable("¿Qué año?", "LiberationSerif"))
        self.assertTrue(_unreliable("½V", "Cmmi10"))
        self.assertEqual(_clean_text("periodo 2016-\n2021 y admi-\nnistración"),
                         "periodo 2016-2021 y administración")

    def test_superscript_subscript_and_dollar(self):
        from lector_pdf_ia.converter import _line_text
        span = lambda t, size, y, flags=0: {"text": t, "size": size, "origin": (0, y),
                                            "flags": flags, "font": "TimesNewRomanPSMT",
                                            "bbox": (0, y - size, 10, y)}
        line = {"spans": [span("abril de 2016.", 12, 100), span("119", 7, 95, 1),
                          span("Huelgan subió $5 y H", 12, 100), span("2", 7, 103), span("O", 12, 100)]}
        self.assertEqual(_line_text(line, None, {}), "abril de 2016.^{119} Huelgan subió \\$5 y H_{2}O")
