"""Extracción incremental de PDF a texto y anexos visuales.

Los archivos finales comparten una sola carpeta. El estado parcial vive en la
carpeta de datos de la aplicación, fuera de la carpeta de exportación.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote

try:  # PyMuPDF >= 1.24 expone "pymupdf"; "fitz" queda como alias obsoleto.
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz


class Cancelled(Exception):
    pass


class Shutdown(Exception):
    pass


def app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / "LectorPDFIA"
    path.mkdir(parents=True, exist_ok=True)
    return path


def book_identity(source: Path) -> tuple[str, str, str]:
    label = re.sub(r"\s+", " ", source.stem).strip() or "Libro"
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(". ") or "Libro"
    # Evita colisiones entre dos rutas con idéntico nombre de archivo.
    digest = hashlib.sha256(str(source.resolve()).casefold().encode()).hexdigest()[:8]
    prefix = f"{label[:90]}__{digest}"
    return label, prefix, digest


def _atomic_text(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _bundle_roots() -> list[Path]:
    """Carpetas donde puede vivir Tesseract empaquetado (PyInstaller onedir)."""
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    return roots


def find_tesseract() -> tuple[str | None, str | None]:
    """Devuelve (ejecutable, carpeta tessdata o None). Prioriza la copia incluida."""
    env_exe = os.environ.get("TESSERACT_EXE")
    if env_exe and Path(env_exe).is_file():
        return env_exe, os.environ.get("TESSDATA_PREFIX")
    for root in _bundle_roots():
        for name in ("tesseract.exe", "tesseract"):
            exe = root / "tesseract" / name
            if exe.is_file():
                return str(exe), str(exe.parent / "tessdata")
    return shutil.which("tesseract"), os.environ.get("TESSDATA_PREFIX")


def tesseract_languages() -> set[str]:
    exe, tessdata = find_tesseract()
    if not exe:
        return set()
    if tessdata and Path(tessdata).is_dir():
        return {p.stem for p in Path(tessdata).glob("*.traineddata")}
    proc = subprocess.run([exe, "--list-langs"], capture_output=True, timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = proc.stdout.decode("utf-8", "replace").splitlines()[1:]
    return {x.strip() for x in lines if x.strip()}


def _ocr(pix: fitz.Pixmap, languages: str) -> str:
    exe, tessdata = find_tesseract()
    if not exe:
        raise RuntimeError("OCR requerido, pero Tesseract no está instalado o no se encuentra.")
    env = os.environ.copy()
    if tessdata:
        env["TESSDATA_PREFIX"] = tessdata
    # Archivo temporal en vez de stdin: la lectura binaria por stdin no es
    # fiable en todas las compilaciones de Tesseract para Windows.
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="lectorpdfia_ocr_")
    os.close(fd)
    try:
        pix.save(tmp, output="png")
        proc = subprocess.run(
            [exe, tmp, "stdout", "-l", languages, "--psm", "3"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300, check=False, env=env, cwd=str(Path(exe).parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    if proc.returncode:
        raise RuntimeError("Falló el OCR: " + proc.stderr.decode("utf-8", "replace")[:400])
    return proc.stdout.decode("utf-8", "replace")


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _header_patterns(doc: fitz.Document) -> set[str]:
    """Detecta líneas cortas repetidas junto al borde en muestra acotada."""
    seen: Counter[str] = Counter()
    for i in range(min(len(doc), 24)):
        p = doc[i]
        unique = set()
        for b in p.get_text("blocks"):
            if b[6] != 0 or not (b[1] < p.rect.height * .09 or b[3] > p.rect.height * .92):
                continue
            line = " ".join(b[4].split())
            if 3 <= len(line) <= 110:
                unique.add(re.sub(r"\d+", "#", line.casefold()))
        seen.update(unique)
    return {line for line, count in seen.items() if count >= 4}


def _text_blocks(page: fitz.Page, repeated: set[str]) -> str:
    segments = []
    for b in page.get_text("blocks", sort=True):
        if b[6] != 0:
            continue
        line = " ".join(b[4].split())
        key = re.sub(r"\d+", "#", line.casefold())
        edge = b[1] < page.rect.height * .09 or b[3] > page.rect.height * .92
        if edge and key in repeated:
            continue
        segments.append(b[4].strip())
    return _clean_text("\n\n".join(segments))


def _visual_regions(page: fitz.Page, scanned: bool) -> list[fitz.Rect]:
    if scanned:
        # En una página escaneada no se puede separar con seguridad una gráfica
        # del resto; se conserva la página íntegra en el anexo.
        return [page.rect]
    area = page.rect.get_area()
    regions = []
    for item in page.get_image_info():
        rect = fitz.Rect(item["bbox"]) & page.rect
        if rect.width >= 55 and rect.height >= 45 and rect.get_area() >= area * .018:
            regions.append(rect)
    try:
        clusters = page.cluster_drawings()
    except (AttributeError, ValueError):
        clusters = []
    for box in clusters:
        rect = fitz.Rect(box) & page.rect
        if rect.width >= 110 and rect.height >= 65 and rect.get_area() >= area * .055:
            regions.append(rect)
    # Un gráfico rasterizado y sus líneas vectoriales pueden solaparse.
    regions.sort(key=lambda r: r.get_area(), reverse=True)
    distinct = []
    for rect in regions:
        if not any((rect & old).get_area() / max(1, rect.get_area()) > .72 for old in distinct):
            distinct.append(rect)
    return distinct[:20]


def _save_figure(page: fitz.Page, box: fitz.Rect, dest: Path) -> None:
    clip = fitz.Rect(box)
    clip.x0 = max(page.rect.x0, clip.x0 - 4)
    clip.y0 = max(page.rect.y0, clip.y0 - 4)
    clip.x1 = min(page.rect.x1, clip.x1 + 4)
    clip.y1 = min(page.rect.y1, clip.y1 + 4)
    pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), clip=clip, alpha=False)
    tmp = dest.with_name(dest.name + ".tmp")
    pix.save(str(tmp), output="png")
    os.replace(tmp, dest)


def _read_checkpoint(path: Path, signature: dict) -> list[dict]:
    if not path.exists():
        return []
    with path.open("rb+") as f:
        header = f.readline()
        try:
            valid = json.loads(header).get("signature") == signature
        except (ValueError, TypeError):
            valid = False
        if not valid:
            f.close()
            path.unlink(missing_ok=True)
            return []
        records = []
        valid_end = f.tell()
        for line in f:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                break
            if row.get("page") != len(records) + 1:
                break
            records.append(row)
            valid_end = f.tell()
        f.truncate(valid_end)
        return records


def _write_checkpoint(path: Path, signature: dict, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"signature": signature}, ensure_ascii=False) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _append_checkpoint(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _visual_pdf(rows: list[dict], folder: Path, label: str, prefix: str) -> list[str]:
    figures = [fig for row in rows for fig in row["figures"]]
    produced = []
    pdf = fitz.open()
    approximate_bytes = 0

    def flush():
        nonlocal pdf, approximate_bytes
        if len(pdf):
            name = f"{prefix}__figuras_{len(produced) + 1:03d}.pdf"
            tmp = folder / (name + ".tmp")
            pdf.save(str(tmp), garbage=4, deflate=True)
            os.replace(tmp, folder / name)
            produced.append(name)
        pdf.close()
        pdf = fitz.open()
        approximate_bytes = 0

    for fig in figures:
        image_path = folder / fig["file"]
        if not image_path.exists():
            continue
        pix = fitz.Pixmap(str(image_path))
        jpeg = pix.tobytes("jpeg", jpg_quality=78)
        if len(pdf) >= 60 or (len(pdf) and approximate_bytes + len(jpeg) > 19_000_000):
            flush()
        page = pdf.new_page(width=595, height=842)
        page.insert_text((36, 40), f'{fig["id"]} | Página {fig["page"]}', fontsize=11)
        ratio = min(523 / pix.width, 760 / pix.height)
        rect = fitz.Rect(36, 55, 36 + pix.width * ratio, 55 + pix.height * ratio)
        page.insert_image(rect, stream=jpeg)
        approximate_bytes += len(jpeg)
    flush()
    return produced


def convert(
    source: str | Path, output: str | Path, *, languages: str = "spa+eng",
    max_pages: int = 40, max_chars: int = 100_000,
    progress=None, checkpoint_root: Path | None = None,
) -> dict:
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Selecciona un archivo PDF existente.")
    output.mkdir(parents=True, exist_ok=True)
    label, prefix, digest = book_identity(source)
    stat = source.stat()
    signature = {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                 "output": str(output), "lang": languages}
    cache_dir = checkpoint_root or app_data() / "checkpoints"
    cache_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = cache_dir / f"{digest}.jsonl"
    rows = _read_checkpoint(checkpoint, signature)
    with fitz.open(source) as doc:
        total = len(doc)
        if not total:
            raise ValueError("El PDF no contiene páginas.")
        if doc.needs_pass:
            raise ValueError("El PDF está protegido por contraseña.")
        if len(rows) > total:
            rows = []
        if not checkpoint.exists():
            _write_checkpoint(checkpoint, signature, rows)
        repeated = _header_patterns(doc)
        figure_no = sum(len(row["figures"]) for row in rows)
        for index in range(len(rows), total):
            if progress:
                progress(index, total)
            page = doc[index]
            text = _text_blocks(page, repeated)
            ocr_error = None
            scanned = len(re.findall(r"\w", text)) < 45
            if scanned:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                try:
                    ocr_text = _clean_text(_ocr(pix, languages))
                    if len(ocr_text) >= len(text) * .8:
                        text = ocr_text or text
                except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                    ocr_error = str(exc)[:300]
                    text = text + f"\n\n[OCR pendiente: {ocr_error}]"
            figures = []
            math_page = bool(re.search(r"[∑∫√∂∇∞≈≠≤≥∈∉∪∩]|\b(?:ecuaci[oó]n|equation)\s*\d", text, re.I))
            for rect in _visual_regions(page, scanned or math_page):
                figure_no += 1
                image_id = f"Imagen {figure_no} ({label})"
                file_name = f"{digest}__Imagen {figure_no:04d} ({label[:90]}).png"
                _save_figure(page, rect, output / file_name)
                figures.append({"id": image_id, "file": file_name, "page": index + 1})
            row = {"page": index + 1, "text": text or "[Página sin texto reconocible]",
                   "figures": figures, "ocr": scanned, "ocr_error": ocr_error}
            _append_checkpoint(checkpoint, row)
            rows.append(row)
    if progress:
        progress(total, total)

    parts = []
    batch = []
    count = 0
    for row in rows:
        size = len(row["text"])
        if batch and (len(batch) >= max_pages or count + size > max_chars):
            parts.append(batch)
            batch, count = [], 0
        batch.append(row)
        count += size
    if batch:
        parts.append(batch)
    names = []
    for number, batch in enumerate(parts, 1):
        filename = f"{prefix}__parte_{number:03d}.md"
        content = [f"# {label} — parte {number}\n\n",
                   f"Fuente: {source.name}. Páginas {batch[0]['page']}–{batch[-1]['page']}.\n\n"]
        for row in batch:
            content.append(f"\n---\n\n## Página {row['page']}\n\n{row['text']}\n\n")
            for fig in row["figures"]:
                content.append(f"**{fig['id']}** (página {row['page']}). "
                               f"![{fig['id']}]({quote(fig['file'])})\n\n")
        _atomic_text(output / filename, "".join(content))
        names.append(filename)
    visual_names = _visual_pdf(rows, output, label, prefix)
    index_name = f"{prefix}__INDICE.md"
    index = [f"# Índice: {label}\n\nFuente: `{source.name}` · {total} páginas.\n\n",
             "Texto (en orden):\n\n"]
    index += [f"- [{name}]({quote(name)})\n" for name in names]
    index.append("\nAnexos visuales (consulta opcional):\n\n")
    index += [f"- [{name}]({quote(name)})\n" for name in visual_names]
    index.append("\nLas imágenes aparecen en esta misma carpeta. Las páginas escaneadas "
                 "se conservan íntegras para revisión visual. OCR y fórmulas pueden necesitar revisión humana.\n")
    _atomic_text(output / index_name, "".join(index))
    # Una nueva conversión del mismo origen no debe dejar partes obsoletas.
    expected = set(names + visual_names + [index_name])
    expected.update(fig["file"] for row in rows for fig in row["figures"])
    generated = (list(output.glob(f"{prefix}__parte_*.md"))
                 + list(output.glob(f"{prefix}__figuras_*.pdf"))
                 + list(output.glob(f"{digest}__Imagen *.png")))
    for candidate in generated:
        if candidate.name not in expected:
            candidate.unlink(missing_ok=True)
    checkpoint.unlink(missing_ok=True)
    return {"source": str(source), "pages": total, "figures": sum(len(r["figures"]) for r in rows),
            "ocr_pages": sum(r["ocr"] for r in rows),
            "ocr_failed": sum(1 for r in rows if r.get("ocr_error")), "index": index_name,
            "parts": names, "visual_pdfs": visual_names}
