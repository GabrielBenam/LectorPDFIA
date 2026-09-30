"""Autodiagnóstico del paquete distribuido.

Uso: LectorPDFIA.exe --selftest --report C:\\ruta\\informe.json [--gui]
Código de salida 0 = todo correcto. Pensado para ejecutarse en Windows
limpio después de compilar, sin Python ni Tesseract instalados.
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
import traceback
from pathlib import Path

from .converter import convert, find_tesseract, fitz, latex_engine, tesseract_languages, _ENGINE

REQUIRED_LANGS = {"spa", "eng"}


def make_digital_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Aerodynamic fundamentals and pressure distribution", fontsize=14)
    page.draw_rect(fitz.Rect(100, 120, 450, 360), color=(.1, .1, .1), fill=(.95, .95, .95))
    for i in range(5):
        page.draw_line((120, 320 - i * 35), (420, 280 - i * 20), color=(0, 0, 1))
    doc.save(path)
    doc.close()


def make_scanned_pdf(path: Path, text: str = "ALA DE AVIÓN Y SUSTENTACIÓN") -> None:
    """PDF con una sola imagen rasterizada (sin capa de texto), como un escaneo."""
    src = fitz.open()
    page = src.new_page(width=900, height=375)
    page.insert_text((40, 200), text, fontsize=40)
    png = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
    src.close()
    doc = fitz.open()
    page = doc.new_page(width=900, height=375)
    page.insert_image(page.rect, stream=png)
    doc.save(path)
    doc.close()


def verify_outputs(output: Path, result: dict) -> list[str]:
    """Comprueba la estructura por libro y abre cada archivo exportado."""
    problems = []
    folder = Path(result["folder"])
    loose = [p.name for p in output.iterdir() if p.is_file()]
    if loose:
        problems.append(f"Archivos sueltos fuera de la carpeta del libro: {loose}")
    expected = [folder / result["merged"], folder / Path(result["source"]).name,
                *(folder / "partes" / n for n in result["parts"]),
                *(folder / n for n in result["visual_pdfs"])]
    for path in expected:
        if not path.is_file():
            problems.append(f"Falta {path.name}")
    for path in [folder / result["merged"], *(folder / "partes" / n for n in result["parts"])]:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            problems.append(f"No se pudo leer {path.name}: {exc}")
    for path in [folder / Path(result["source"]).name, *(folder / n for n in result["visual_pdfs"])]:
        try:
            with fitz.open(path) as d:
                if not len(d):
                    problems.append(f"{path.name} no tiene páginas")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"No se pudo abrir {path.name}: {exc}")
    pngs = list((folder / "imagenes").glob("*.png"))
    if len(pngs) != result["figures"]:
        problems.append(f"Se esperaban {result['figures']} PNG y hay {len(pngs)}")
    for png in pngs:
        try:
            fitz.Pixmap(str(png))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"PNG inválido {png.name}: {exc}")
    return problems


def run(report: Path | None = None, gui: bool = False) -> int:
    checks: list[dict] = []

    def check(name, fn):
        try:
            detail = fn()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001
            checks.append({"check": name, "ok": False, "detail": f"{exc}",
                           "trace": traceback.format_exc()[-1500:]})

    def tesseract():
        exe, tessdata = find_tesseract()
        if not exe:
            raise RuntimeError("Tesseract no encontrado")
        if getattr(sys, "frozen", False) and not any(
                str(Path(exe).resolve()).startswith(str(r.resolve())) for r in _roots()):
            raise RuntimeError(f"Se usa un Tesseract externo, no el incluido: {exe}")
        return {"exe": exe, "tessdata": tessdata}

    def langs():
        found = tesseract_languages()
        missing = REQUIRED_LANGS - found
        if missing:
            raise RuntimeError(f"Faltan idiomas OCR: {sorted(missing)}; hay {sorted(found)}")
        return sorted(found)

    with tempfile.TemporaryDirectory(prefix="lectorpdfia_selftest_") as tmp:
        root = Path(tmp)

        def digital():
            src = root / "Gudmundsson.pdf"
            make_digital_pdf(src)
            out = root / "salida"
            res = convert(src, out, languages="spa+eng", checkpoint_root=root / "cp")
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            assert "Aerodynamic fundamentals" in md, "texto digital ausente"
            assert "Imagen 1 (Gudmundsson)" in md, "identificador de imagen ausente"
            assert res["ocr_pages"] == 0, "página digital enviada a OCR"
            probs = verify_outputs(out, res)
            assert not probs, probs
            return {k: res[k] for k in ("pages", "figures", "parts", "visual_pdfs")}

        def scanned():
            src = root / "Escaneado prueba.pdf"
            make_scanned_pdf(src)
            out = root / "salida"
            res = convert(src, out, languages="spa+eng", checkpoint_root=root / "cp")
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            assert res["ocr_pages"] == 1 and not res["ocr_failed"], f"OCR falló: {md[-400:]}"
            upper = md.upper()
            assert "SUSTENTACI" in upper and "AVI" in upper, f"OCR no reconoció el texto: {md[-300:]}"
            assert "Imagen 1 (Escaneado prueba)" in md, "identificador de imagen ausente"
            probs = verify_outputs(out, res)
            assert not probs, probs
            return {"ocr_excerpt": md.split("[p. 1]", 1)[-1][:200].strip()}

        def formulas():
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            src = base / "prueba_formulas.pdf"
            if not src.is_file():
                raise RuntimeError(f"Falta {src}")
            if latex_engine() is None:
                raise RuntimeError("Motor de fórmulas no disponible: " + _ENGINE.get("error", ""))
            res = convert(src, root / "formulas", languages="eng", checkpoint_root=root / "cp")
            md = (Path(res["folder"]) / res["merged"]).read_text(encoding="utf-8")
            squash = md.replace(" ", "").replace("{", "").replace("}", "")
            for piece in (r"\frac2L\rhoV^2S", r"\tag3.1", r"\sigma=\fracMyI", r"\sum_i=1^n"):
                if piece not in squash:
                    raise RuntimeError(f"LaTeX esperado no encontrado ({piece}): {md[-600:]}")
            return {"ecuaciones": res["equations"], "por_verificar": res["equations_flagged"]}

        def window():
            from .app import Application
            from .jobs import JobStore
            import os
            os.environ["LOCALAPPDATA"] = str(root / "appdata")
            app = Application()
            app.update()
            title = app.title()
            app._close()
            JobStore(root / "appdata" / "LectorPDFIA" / "jobs.sqlite3")
            return {"title": title, "arrastrar_y_soltar": bool(getattr(app, "dnd", False))}

        check("tesseract_incluido", tesseract)
        check("idiomas_spa_eng", langs)
        check("pdf_digital", digital)
        check("pdf_escaneado_ocr", scanned)
        check("formulas_latex", formulas)
        if gui:
            check("ventana_abre", window)

    ok = all(c["ok"] for c in checks)
    data = {"ok": ok, "frozen": bool(getattr(sys, "frozen", False)),
            "platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version, "checks": checks}
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        print(text)
    return 0 if ok else 1


def _roots():
    from .converter import _bundle_roots
    return _bundle_roots()
