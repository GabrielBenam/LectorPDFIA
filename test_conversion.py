import tempfile
import time
import unittest
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

from lector_pdf_ia.converter import Cancelled, convert
from lector_pdf_ia.jobs import JobStore, QueueWorker


class ConversionTest(unittest.TestCase):
    def test_text_image_and_flat_export(self):
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
            output = root / "export"
            result = convert(src, output, languages="eng", checkpoint_root=root / "checkpoints")
            self.assertEqual(result["pages"], 1)
            self.assertGreaterEqual(result["figures"], 1)
            self.assertTrue(result["visual_pdfs"])
            md = (output / result["parts"][0]).read_text(encoding="utf-8")
            self.assertIn("Aerodynamic fundamentals", md)
            self.assertIn("Imagen 1 (Gudmundsson)", md)
            self.assertIn("Página 1", md)
            self.assertTrue(all(p.is_file() for p in output.iterdir()))
            self.assertTrue(any(p.name.endswith("(Gudmundsson).png") for p in output.iterdir()))

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
            result = convert(src, root / "out", languages="eng", checkpoint_root=root / "checkpoints")
            text = (root / "out" / result["parts"][0]).read_text(encoding="utf-8")
            self.assertEqual(result["ocr_pages"], 1)
            self.assertIn("AIRCRAFT", text)
            self.assertIn("Imagen 1 (Scan)", text)

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
            self.assertNotEqual(first["parts"][0], second["parts"][0])
            self.assertIn("Volume first", (root / "out" / first["parts"][0]).read_text())
            self.assertIn("Volume second", (root / "out" / second["parts"][0]).read_text())

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
            self.assertEqual(store.all()[0]["state"], "done", store.all()[0]["message"])


if __name__ == "__main__":
    unittest.main()
