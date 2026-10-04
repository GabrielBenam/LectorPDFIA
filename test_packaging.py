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
            self.assertIn("SUSTENTACI", md.upper())
            self.assertIn("Imagen 0001 (Libro español).png", md)
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
            env["PYTHONUTF8"] = "1"
            proc = subprocess.run([sys.executable, str(ROOT / "start.py"), "--cli", str(src),
                                   "--output", str(tmp / "out"), "--lang", "eng"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  cwd=ROOT, env=env, timeout=120)
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
            self.assertEqual(res["equations"], 3)                  # las 3 pasan a LaTeX
            self.assertLessEqual(res["equations_flagged"], 1)
            found = [p for p in (r"\frac2L\rhoV^2S", r"\tag3.1", r"\fracMyI", r"\sum_i=1^n",
                                 r"\tag3.2") if p in squash]
            self.assertGreaterEqual(len(found), 4, md)
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
                self.assertIn("Ecuacion 0001 (prueba_formulas).png", md)
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


class RobustnessTest(unittest.TestCase):
    def test_text_fonts_of_math_families_are_not_math(self):
        from lector_pdf_ia.converter import MATH_FONT
        for font in ("STIX-Regular", "STIX-Italic", "STIXGeneral", "XITS-Regular", "Cambria"):
            self.assertIsNone(MATH_FONT.search(font), font)
        for font in ("STIXMath-Regular", "STIXMathExtensions-Regul", "CambriaMath", "Cmmi10"):
            self.assertIsNotNone(MATH_FONT.search(font), font)

    def test_readable_formula_stays_text_without_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((60, 80), "La media se define como sigue.", fontsize=11)
            page.insert_text((200, 120), "m = E(X) = 1/p", fontsize=11, fontname="symb")
            doc.save(tmp / "Media.pdf")
            called = []
            with mock.patch.object(converter, "formula_to_latex",
                                   side_effect=lambda *a: called.append(1) or ("", False)):
                res = convert(tmp / "Media.pdf", tmp / "out", languages="eng",
                              checkpoint_root=tmp / "cp")
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            self.assertEqual(called, [])                   # no se llamó al modelo
            self.assertEqual(res["math_text"], 1)
            self.assertEqual(res["equations_flagged"], 0)
            self.assertNotIn("$$", md.split("\n---\n", 1)[1])

    def test_fully_scanned_book_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            single = tmp / "una.pdf"
            make_scanned_pdf(single, "PAGINA")
            png = fitz.open(single)[0].get_pixmap().tobytes("png")
            doc = fitz.open()
            for _ in range(25):
                p = doc.new_page(width=600, height=250)
                p.insert_image(p.rect, stream=png)
            doc.save(tmp / "Escaneado.pdf")
            with self.assertRaises(converter.ScannedDocument) as ctx:
                convert(tmp / "Escaneado.pdf", tmp / "out", languages="eng",
                        checkpoint_root=tmp / "cp")
            self.assertIn("escaneado", str(ctx.exception).lower())

    def test_auto_cancel_after_time_limit_with_low_progress(self):
        import time
        from lector_pdf_ia import jobs
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            doc = fitz.open()
            for i in range(30):
                doc.new_page().insert_text((60, 80), f"Página {i} con texto suficiente.")
            doc.save(tmp / "Lento.pdf")
            store = jobs.JobStore(tmp / "q.sqlite3")
            store.add(tmp / "Lento.pdf", tmp / "out", "eng")
            with mock.patch.object(jobs, "AUTO_CANCEL_SECONDS", 0):
                worker = jobs.QueueWorker(store)
                worker.start()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and store.all()[0]["state"] in ("queued", "running"):
                    time.sleep(.05)
                worker.stop.set()
                worker.thread.join(timeout=2)
            job = store.all()[0]
            self.assertEqual(job["state"], "cancelled")
            self.assertIn("automáticamente", job["message"])


class LayoutTest(unittest.TestCase):
    def _convert(self, doc, name="Libro"):
        tmp = Path(tempfile.mkdtemp())
        doc.save(tmp / f"{name}.pdf")
        res = convert(tmp / f"{name}.pdf", tmp / "out", languages="eng", checkpoint_root=tmp / "cp")
        return res, (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8").split("\n---\n", 1)[1]

    def test_shaded_box_is_text_not_figure(self):
        doc = fitz.open()
        page = doc.new_page()
        page.draw_rect(fitz.Rect(60, 100, 540, 400), color=None, fill=(.9, .9, .92))
        page.insert_textbox(fitz.Rect(80, 120, 520, 380), "EXAMPLE 1. " + "The take-off distance "
                            "is estimated from the thrust and the drag of the airplane. " * 6, fontsize=11)
        res, md = self._convert(doc)
        self.assertEqual(res["figures"], 0)
        self.assertNotIn("Texto en la figura", md)
        self.assertIn("EXAMPLE 1. The take-off distance", md)

    def test_two_column_reading_order(self):
        doc = fitz.open()
        page = doc.new_page()
        for k in range(3):
            page.insert_textbox(fitz.Rect(50, 100 + k * 80, 290, 170 + k * 80),
                                f"Izquierda {k} " + "texto de la columna izquierda " * 4, fontsize=10)
            page.insert_textbox(fitz.Rect(320, 100 + k * 80, 560, 170 + k * 80),
                                f"Derecha {k} " + "texto de la columna derecha " * 4, fontsize=10)
        res, md = self._convert(doc)
        order = [md.index(f"{side} {k}") for side in ("Izquierda", "Derecha") for k in range(3)]
        self.assertEqual(order, sorted(order))

    def test_numbered_and_split_headings_and_bullets(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((60, 90), "2.1.1 VALUE DELIVERY COMPONENTS", fontsize=11)
        page.insert_text((60, 140), "CUSTOMER- AND END-USER-CENTERED", fontsize=16)
        page.insert_text((60, 158), "DESIGN", fontsize=16)
        page.insert_textbox(fitz.Rect(60, 200, 540, 400), "Cuerpo del texto normal. " * 20, fontsize=11)
        page.draw_polyline([(62, 413), (62, 421), (69, 417), (62, 413)], color=(0, 0, 0), fill=(0, 0, 0))
        page.insert_text((80, 420), "Improving efficiency and productivity;", fontsize=11)
        res, md = self._convert(doc)
        self.assertIn("### 2.1.1 VALUE DELIVERY COMPONENTS", md)
        self.assertIn("## CUSTOMER- AND END-USER-CENTERED DESIGN", md)
        self.assertIn("- Improving efficiency and productivity;", md)

    def test_elsevier_symbols_and_short_image_ids(self):
        from lector_pdf_ia.converter import _fix_symbol
        self.assertEqual(_fix_symbol("C ¼ ðaÞ", "ABCDEF+AdvP4C4E74"), "C = (a)")
        self.assertEqual(_fix_symbol("½ ¼", "TimesNewRomanPSMT"), "½ ¼")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 60), "Figura de prueba", fontsize=12)
        page.draw_rect(fitz.Rect(100, 120, 450, 360), color=(.1, .1, .1), fill=(.95, .95, .95))
        for i in range(5):
            page.draw_line((120, 320 - i * 35), (420, 280 - i * 20), color=(0, 0, 1))
        res, md = self._convert(doc, "Nombre Largo De Libro")
        self.assertIn("![Imagen 1](<imagenes/Imagen 0001 (Nombre Largo De Libro).png>)", md)


class RotatedAndScannedPagesTest(unittest.TestCase):
    def _convert(self, doc, name="Libro", **kw):
        tmp = Path(tempfile.mkdtemp())
        doc.save(tmp / f"{name}.pdf")
        res = convert(tmp / f"{name}.pdf", tmp / "out", languages="eng", checkpoint_root=tmp / "cp", **kw)
        md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
        return res, md.split("\n---\n", 1)[1]

    def test_rotated_page_reads_in_displayed_order(self):
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        for k, word in enumerate(("Primera", "Segunda", "Tercera")):
            page.insert_text((100 + 40 * k, 750), f"{word} linea de la pagina girada con texto suficiente",
                             fontsize=11, rotate=90)
        page.draw_rect(fitz.Rect(300, 100, 520, 400), color=(0, 0, 0), fill=(.9, .9, .9))
        for i in range(6):
            page.draw_line((310, 120 + i * 40), (500, 140 + i * 30), color=(0, 0, 1))
        page.set_rotation(90)
        res, md = self._convert(doc)
        self.assertEqual(res["page_errors"], 0)
        order = [md.index(w) for w in ("Primera", "Segunda", "Tercera")]
        self.assertEqual(order, sorted(order))

    def test_scan_background_is_not_a_figure(self):
        shot = fitz.open()
        sp = shot.new_page(width=600, height=800)
        sp.insert_text((60, 100), "Texto del escaneo", fontsize=14)
        png = sp.get_pixmap(dpi=72).tobytes("png")
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=png)
        body = ("Este es el texto de la capa oculta de un reconocimiento previo del libro. " * 8)
        page.insert_textbox(fitz.Rect(50, 120, 550, 700), body, fontsize=11, render_mode=3)
        res, md = self._convert(doc)
        self.assertEqual(res["figures"], 0)
        self.assertNotIn("Texto en la figura", md)
        self.assertIn("capa oculta", md)

    def test_empty_or_outside_clip_never_raises(self):
        import numpy as np
        doc = fitz.open()
        page = doc.new_page()
        span = {"text": "x", "size": 11, "font": "Cmmi10", "bbox": (0, 900, 10, 910), "origin": (0, 910),
                "flags": 0}
        self.assertEqual(converter.formula_to_latex(page, fitz.Rect(0, 900, 50, 950), [span]), ("", False))
        if converter.latex_engine() is not None:
            self.assertEqual(converter.latex_engine()(np.zeros((0, 5), np.uint8)), "")

    def test_page_error_falls_back_to_simple_text(self):
        doc = fitz.open()
        for i in range(3):
            doc.new_page().insert_text((60, 300), f"Contenido de la pagina {i + 1} sobre el tema {'ABC'[i] * 5}")
        real = converter._process_page

        def flaky(page, index, *a, **kw):
            if index == 1:
                raise ValueError("zero-size array to reduction operation")
            return real(page, index, *a, **kw)

        with mock.patch.object(converter, "_process_page", side_effect=flaky):
            res, md = self._convert(doc)
        self.assertEqual(res["page_errors"], 1)
        self.assertIn("Contenido de la pagina 2", md)
        self.assertIn("modo simple", md)
        self.assertIn("Contenido de la pagina 3", md)

    def test_ocr_noise_lines_are_dropped(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((60, 80), "Texto real de la pagina con palabras suficientes.", fontsize=11)
        for k in range(4):
            page.insert_text((300, 120 + 15 * k), "(", fontsize=11)
        res, md = self._convert(doc)
        self.assertIn("Texto real", md)
        self.assertNotIn("\n\n(\n\n", md)
