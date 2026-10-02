import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lector_pdf_ia.converter import Cancelled, convert, fitz
from lector_pdf_ia.jobs import JobStore, QueueWorker


def book_pdf(path: Path, chapters=3, pages=8):
    """Libro con los defectos vistos en PDFs reales: encabezados por capítulo,
    pie repetido, folios, título en capa duplicada, tabla, diagrama y separadora."""
    doc = fitz.open()
    n = 0
    total = chapters * pages
    for c in range(1, chapters + 1):
        for k in range(pages):
            n += 1
            p = doc.new_page()
            p.insert_text((60, 40), f"{c}.- Capítulo número {c} del libro", fontsize=9)
            p.insert_text((60, 52), f"pág. {n} de {total}", fontsize=9)
            p.insert_text((200, 815), "La Habana: Editorial Universitaria", fontsize=9)
            y = 110
            if k == 0:
                for _ in range(2):
                    p.insert_text((60, y), f"CAPÍTULO {c}", fontsize=20)
                y += 40
            p.insert_textbox(fitz.Rect(60, y, 530, y + 120),
                             f"Texto del capítulo {c}, página {n}. La gestión pública requiere "
                             "procesos transparentes y medibles para la ciudadanía.", fontsize=11)
            if c == 2 and k == 2:
                p.insert_text((80, 290), "Tabla 1. Índice de gobierno electrónico", fontsize=10)
                rows = [["País", "IDGE", "Posición"], ["Cuba", "0.3917", "116"],
                        ["Chile", "0.7122", "33"], ["Uruguay", "0.7420", "26"]]
                for r, row in enumerate(rows):
                    for col, v in enumerate(row):
                        rect = fitz.Rect(80 + col * 140, 300 + r * 22, 220 + col * 140, 322 + r * 22)
                        p.draw_rect(rect, color=(0, 0, 0), width=.8)
                        p.insert_text((rect.x0 + 5, rect.y1 - 7), v, fontsize=10)
            if c == 1 and k == 3:
                for i, t in enumerate(["Consejo de Ministros", "Organismos Globales", "OSDE"]):
                    r = fitz.Rect(150, 300 + i * 70, 400, 340 + i * 70)
                    p.draw_rect(r, color=(.2, .5, .2), fill=(.8, .9, .7), width=1)
                    for _ in range(2):
                        p.insert_text((r.x0 + 20, r.y0 + 25), t, fontsize=11)
                    if i:
                        p.draw_line((275, r.y0 - 30), (275, r.y0), color=(0, 0, 0), width=1.5)
                p.insert_text((150, 518), "Figura 1: Organigrama institucional", fontsize=10)
    doc.new_page(pno=5).insert_text((250, 400), "DESARROLLO", fontsize=16)
    doc.save(path)
    doc.close()


class ConversionTest(unittest.TestCase):
    def test_book_folder_export(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            src = root / "Gudmundsson.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((50, 60), "Aerodynamic fundamentals and pressure distribution", fontsize=14)
            page.draw_rect(fitz.Rect(100, 120, 450, 360), color=(.1, .1, .1), fill=(.95, .95, .95))
            for i in range(5):
                page.draw_line((120, 320 - i * 35), (420, 280 - i * 20), color=(0, 0, 1))
            doc.save(src)
            doc.close()
            out = root / "export"
            res = convert(src, out, languages="eng", checkpoint_root=root / "cp")
            folder = Path(res["folder"])
            self.assertTrue(folder.samefile(out / "Gudmundsson"))   # 8.3 vs. nombre largo
            md = (folder / res["merged"]).read_text(encoding="utf-8")
            self.assertIn("Aerodynamic fundamentals", md)
            self.assertIn("Imagen 0001 (Gudmundsson).png", md)
            self.assertIn("[p. 1]", md)
            self.assertTrue((folder / "Gudmundsson.pdf").is_file())
            self.assertTrue((folder / "imagenes" / "Imagen 0001 (Gudmundsson).png").is_file())
            self.assertTrue((folder / "partes" / res["parts"][0]).is_file())
            self.assertTrue((folder / res["visual_pdfs"][0]).is_file())
            self.assertEqual([p for p in out.iterdir() if p.is_file()], [])
            part = (folder / "partes" / res["parts"][0]).read_text(encoding="utf-8")
            self.assertIn("](<../imagenes/Imagen 0001 (Gudmundsson).png>)", part)
            self.assertNotIn(b"\r\n", (folder / res["merged"]).read_bytes())

    def test_cleaning_tables_and_structure(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            src = root / "Libro.pdf"
            book_pdf(src)
            res = convert(src, root / "out", languages="spa+eng", checkpoint_root=root / "cp",
                          max_pages=10)
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            self.assertNotIn("Editorial Universitaria", md)          # pie repetido
            self.assertNotIn("pág. 3 de", md)                        # folio
            self.assertNotIn("Capítulo número 2 del libro", md)      # encabezado de capítulo
            self.assertEqual(md.count("## CAPÍTULO 1"), 1)           # capa duplicada
            self.assertIn("- p. 1: CAPÍTULO 1", md)                  # índice de contenido
            self.assertIn("| Cuba | 0.3917 | 116 |", md)             # tabla en Markdown
            self.assertEqual(res["tables"], 1)                       # el diagrama no es tabla
            self.assertIn("Texto en la figura: Consejo de Ministros · Organismos Globales · OSDE\n", md)
            self.assertEqual(res["ocr_pages"], 0)                    # separadora sin OCR
            self.assertLess(md.index("Tabla 1."), md.index("| País |"))
            self.assertEqual(len(res["parts"]), 3)
            self.assertIn("[p. 25]", md)

    def test_large_parts_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            src = root / "Libro.pdf"
            book_pdf(src, chapters=2, pages=6)
            res = convert(src, root / "out", languages="eng", checkpoint_root=root / "cp",
                          max_pages=2000)
            self.assertEqual(len(res["parts"]), 1)

    def test_scanned_ocr(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            picture = Image.new("RGB", (1600, 750), "white")
            draw = ImageDraw.Draw(picture)
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 72)
            except OSError:
                font = ImageFont.truetype("arial.ttf", 72)
            draw.text((100, 200), "AIRCRAFT WING THEORY", fill="black", font=font)
            image = root / "scan.png"
            picture.save(image)
            doc = fitz.open()
            page = doc.new_page(width=800, height=375)
            page.insert_image(page.rect, filename=str(image))
            src = root / "Scan.pdf"
            doc.save(src)
            doc.close()
            res = convert(src, root / "out", languages="eng", checkpoint_root=root / "cp")
            text = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            self.assertEqual(res["ocr_pages"], 1)
            self.assertIn("AIRCRAFT", text)
            self.assertIn("Imagen 0001 (Scan).png", text)

    def test_queue_survives_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / "queue.sqlite3"
            store = JobStore(database)
            key = store.add(Path(root) / "book.pdf", Path(root) / "output", "eng")
            job = store.next()
            self.assertEqual(job["id"], key)
            self.assertEqual(JobStore(database).all()[0]["state"], "queued")

    def test_resume_and_name_collision(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            sources = []
            for dirname in ("first", "second"):
                folder = root / dirname
                folder.mkdir()
                src = folder / "Gudmundsson.pdf"
                doc = fitz.open()
                for i in range(2):
                    p = doc.new_page()
                    p.insert_text((50, 60), f"Volume {dirname} section {i} and mathematical ∑ notation")
                doc.save(src)
                doc.close()
                sources.append(src)

            def interrupt(current, total):
                if current == 1:
                    raise Cancelled()

            cache = root / "checkpoints"
            with self.assertRaises(Cancelled):
                convert(sources[0], root / "out", languages="eng", checkpoint_root=cache,
                        progress=interrupt)
            first = convert(sources[0], root / "out", languages="eng", checkpoint_root=cache)
            second = convert(sources[1], root / "out", languages="eng", checkpoint_root=cache)
            again = convert(sources[0], root / "out", languages="eng", checkpoint_root=cache)
            self.assertNotEqual(first["folder"], second["folder"])
            self.assertEqual(first["folder"], again["folder"])
            read = lambda r: (Path(r["folder"]) / r["merged"]).read_text(encoding="utf-8")
            self.assertIn("Volume first", read(first))
            self.assertIn("Volume second", read(second))

    def test_worker_processes_queue(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            src = root / "Book.pdf"
            doc = fitz.open()
            doc.new_page().insert_text((50, 50), "A short chapter with searchable text")
            doc.save(src)
            doc.close()
            store = JobStore(root / "jobs.sqlite3")
            store.add(src, root / "out", "eng", max_pages=1)
            worker = QueueWorker(store)
            worker.start()
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and store.all()[0]["state"] not in ("done", "failed"):
                time.sleep(.05)
            worker.stop.set()
            worker.thread.join(timeout=2)
            job = store.all()[0]
            self.assertEqual(job["state"], "done", job["message"])
            self.assertTrue(Path(job["folder"]).is_dir())


if __name__ == "__main__":
    unittest.main()
