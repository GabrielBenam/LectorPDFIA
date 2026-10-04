"""Extracción incremental de PDF a Markdown compacto y anexos visuales.

Cada libro se exporta en su propia carpeta:
    <salida>/<Libro>/<Libro> - COMPLETO.md      texto reunido (archivo principal)
    <salida>/<Libro>/<Libro>.pdf                copia del PDF original
    <salida>/<Libro>/<Libro> - figuras NNN.pdf  anexo visual para adjuntar a la IA
    <salida>/<Libro>/imagenes/Imagen NNNN (<Libro>).png
    <salida>/<Libro>/partes/<Libro> - parte NNN.md
El estado parcial (checkpoints) vive en la carpeta de datos de la aplicación.
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
from collections import Counter, deque
from pathlib import Path

try:  # PyMuPDF >= 1.24 expone "pymupdf"; "fitz" queda como alias obsoleto.
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

FORMAT_VERSION = 5          # invalida checkpoints de la versión anterior
EDGE_TOP, EDGE_BOTTOM = .20, .80   # franja amplia; solo se quita lo que se repite
NAME_CHARS = 60             # límite del nombre en archivos (rutas de Windows < 260)
CAPTION = re.compile(r"^\s*(figura|fig\.|gr[aá]fic[oa]|tabla|cuadro|esquema|diagrama|"
                     r"ilustraci[oó]n|figure|table|chart|fuente|source)\b", re.I)
PAGE_NUMBER = re.compile(r"^\W*(p[aá]g(ina)?\.?\s*)?\d{1,4}(\s*(de|of|/)\s*\d{1,4})?\W*$", re.I)


class Cancelled(Exception):
    pass


class Shutdown(Exception):
    pass


class ScannedDocument(ValueError):
    """El PDF es casi todo imagen: el OCR de todo el libro tardaría demasiado."""


SCAN_MIN_PAGES, SCAN_RATIO = 20, .8


def _looks_scanned(page) -> bool:
    words = len(re.findall(r"\w", page.get_text("text")))
    return words < 45 and _image_coverage(page) >= .5


def check_not_scanned(doc) -> None:
    total = len(doc)
    if total <= SCAN_MIN_PAGES:
        return
    sample = sorted({int(i * (total - 1) / 29) for i in range(30)})
    scanned = sum(_looks_scanned(doc[i]) for i in sample)
    if scanned / len(sample) >= SCAN_RATIO:
        raise ScannedDocument(
            f"PDF escaneado: {round(100 * scanned / len(sample))} % de las páginas muestreadas "
            f"no tienen texto. El OCR de {total} páginas tardaría demasiado; conversión detenida.")


def app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / "LectorPDFIA"
    path.mkdir(parents=True, exist_ok=True)
    return path


def book_identity(source: Path) -> tuple[str, str]:
    """(nombre legible del libro, huella corta de su ruta)."""
    label = re.sub(r"\s+", " ", source.stem).strip() or "Libro"
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(". ") or "Libro"
    digest = hashlib.sha256(str(source.resolve()).casefold().encode()).hexdigest()[:8]
    return label, digest


def _short(label: str) -> str:
    return label[:NAME_CHARS].rstrip(" .") or "Libro"


def _write_text(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:  # LF: menos bytes que CRLF
        f.write(data)
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


# --------------------------------------------------------------------------- texto

def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.split()).casefold())


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\u00ad", "").replace("\r", "")
    text = re.sub(r"(?<=[^\W\d_])-\n(?=[a-záéíóúüñ])", "", text)   # corte de palabra
    text = re.sub(r"-\n(?=\S)", "-", text)                       # 2016-\n2021 -> 2016-2021
    text = re.sub(r"[ \t]*\n[ \t]*(?=\S)", "\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)           # une líneas del párrafo
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _in_edge(y0: float, y1: float, height: float) -> bool:
    return y1 <= height * EDGE_TOP or y0 >= height * EDGE_BOTTOM


def _edge_indices(rects: list, height: float) -> set[int]:
    """Bloques del encabezado y del pie: el primer y el último grupo de renglones de la
    página (sin hueco mayor a un renglón) dentro de la franja superior o inferior."""
    edge: set[int] = set()
    order = sorted(range(len(rects)), key=lambda i: rects[i][1])
    bottom_line = None
    for i in order:
        r = rects[i]
        if r[1] > height * EDGE_TOP:
            break
        if bottom_line is not None and r[1] - bottom_line > max(4.0, r[3] - r[1]):
            break
        edge.add(i)
        bottom_line = r[3] if bottom_line is None else max(bottom_line, r[3])
    top_line = None
    for i in sorted(range(len(rects)), key=lambda i: -rects[i][3]):
        r = rects[i]
        if r[3] < height * EDGE_BOTTOM:
            break
        if top_line is not None and top_line - r[3] > max(4.0, r[3] - r[1]):
            break
        edge.add(i)
        top_line = r[1] if top_line is None else min(top_line, r[1])
    return edge


def analyze_document(doc) -> dict:
    """Encabezados/pies repetidos (en todo el libro) y tamaño de letra del cuerpo."""
    total = len(doc)
    counts: Counter[str] = Counter()
    sizes: Counter[int] = Counter()
    sample = set(range(0, total, max(1, total // 60)))
    for i in range(total):
        page = doc[i]
        height = page.rect.height
        seen = set()
        text_blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        mat = page.rotation_matrix
        for k in _edge_indices([tuple(fitz.Rect(b[:4]) * mat) for b in text_blocks], height):
            b = text_blocks[k]
            if True:
                for line in b[4].splitlines():
                    key = _norm(line)
                    if 2 <= len(key) <= 120:
                        seen.add(key)
        counts.update(seen)
        if i in sample:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        sizes[round(span["size"])] += len(span["text"].strip())
    threshold = max(3, round(total * .03))
    return {"repeated": {k for k, c in counts.items() if c >= threshold},
            "body": (sizes.most_common(1)[0][0] if sizes else 11) or 11}


def _image_coverage(page) -> float:
    area = page.rect.get_area() or 1
    covered = 0.0
    mat = page.rotation_matrix              # imágenes en coordenadas sin girar -> visibles
    for item in page.get_image_info():
        covered += ((fitz.Rect(item["bbox"]) * mat) & page.rect).get_area()
    return min(1.0, covered / area)


def _dominant_dir(page) -> tuple[int, int]:
    """Dirección en que corre la mayor parte del texto: (1, 0) = horizontal normal."""
    votes: Counter = Counter()
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            n = sum(len(sp["text"]) for sp in line["spans"])
            votes[(round(line["dir"][0]), round(line["dir"][1]))] += n
    return votes.most_common(1)[0][0] if votes else (1, 0)


_ANGLE_FOR_DIR = {(0, -1): 270, (0, 1): 90, (-1, 0): 180}


class UprightPages:
    """Entrega cada página con el texto horizontal y las coordenadas de texto, imágenes,
    dibujos y recortes en un mismo sistema. Los PDF escaneados suelen traer /Rotate 90 y
    el texto en vertical: PyMuPDF entrega entonces el texto sin girar y los recortes
    girados, y todo (fórmulas, figuras, orden de lectura) queda desalineado."""

    def __init__(self, doc):
        self.doc = doc
        self._tmp = None

    def page(self, index: int):
        page = self.doc[index]
        if not page.rotation:
            return page
        try:
            page.set_rotation(0)
            direction = _dominant_dir(page)
            if direction == (1, 0):
                return page                                  # texto ya horizontal sin giro
            first = _ANGLE_FOR_DIR.get(direction, 270)
            width, height = page.rect.width, page.rect.height
            for angle in dict.fromkeys((first, 270, 90, 180)):
                if self._tmp is not None:
                    self._tmp.close()
                self._tmp = fitz.open()
                new = self._tmp.new_page(-1, width=height, height=width)
                new.show_pdf_page(new.rect, self.doc, index, rotate=angle)
                if _dominant_dir(new) == (1, 0):
                    return new
        except Exception:  # noqa: BLE001 - mejor la página sin enderezar que ninguna
            pass
        return self.doc[index]

    def close(self):
        if self._tmp is not None:
            self._tmp.close()
            self._tmp = None


NUMBERED_HEADING = re.compile(r"^((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){0,4})\.?\s+[A-ZÁÉÍÓÚ(][^\n]{2,110}$")


def _line_text(line, page, stats) -> str:
    """Texto de una línea con ^{superíndices}, _{subíndices} y fórmulas en línea."""
    spans = [dict(sp, text=_fix_symbol(sp["text"], sp["font"])) for sp in line["spans"] if sp["text"]]
    if not spans:
        return ""
    big = max(spans, key=lambda sp: sp["size"])
    base, size = big["origin"][1], big["size"]
    out, i = [], 0
    while i < len(spans):
        sp = spans[i]
        if page is not None and _is_math_span(sp):
            j = i
            while j < len(spans) and _is_math_span(spans[j]):
                j += 1
            run = spans[i:j]
            raw = "".join(r["text"] for r in run)
            if raw.strip(BULLETS + " ") and any(_unreliable(r["text"], r["font"]) for r in run) \
                    and len(raw.strip()) >= 2 \
                    and (any(TEX_FONT.search(r["font"]) for r in run) or len(raw.strip()) >= 3):
                rect = fitz.Rect(run[0]["bbox"])
                for r in run[1:]:
                    rect |= fitz.Rect(r["bbox"])
                tex, ok = formula_to_latex(page, rect, run)
                if tex:
                    stats["inline"] = stats.get("inline", 0) + 1
                    out.append(f" ${tex}$ " if ok else f" ${tex}$[?] ")
                    i = j
                    continue
        text = sp["text"].replace("$", "\\$")            # "$" literal, no fórmula
        if text.strip() and sp["size"] < size * .85:
            dy = sp["origin"][1] - base
            nxt = spans[i + 1]["text"][:1] if i + 1 < len(spans) else ""
            if sp["flags"] & 1 or dy < -.15 * size:
                text = "^{" + text.strip() + "}" + (" " if text.strip().isdigit() and nxt.isalpha() else "")
            elif dy > .1 * size:
                text = "_{" + text.strip() + "}"
        out.append(text)
        i += 1
    return re.sub(r" {2,}", " ", "".join(out))


def _block_text(block: dict, height: float, repeated: set[str], body: float,
                skip=frozenset(), bi: int = -1, page=None, stats=None, edge=False) -> str:
    lines, sizes = [], []
    stats = {} if stats is None else stats
    for li, line in enumerate(block["lines"]):
        if (bi, li) in skip:
            continue
        raw = "".join(s["text"] for s in line["spans"])
        if not raw.strip():
            continue
        if not re.search(r"\w", raw) and all(OCR_FONT.search(s["font"]) for s in line["spans"]):
            continue                                      # basura de un OCR previo
        if edge and (_norm(raw) in repeated or PAGE_NUMBER.match(raw)):
            continue                                      # encabezado, pie o folio
        if lines and raw.strip() == lines[-1][1].strip():
            continue                                      # capa de texto duplicada
        lines.append((_line_text(line, page, stats), raw))
        sizes.extend(s["size"] for s in line["spans"] if s["text"].strip())
    text = _clean_text("\n".join(t for t, _ in lines))
    if text and not re.search(r"\w", text) and len(text) <= 12 and text.strip() not in ("•", "-", "*", "–", "—", "·"):
        return ""                                         # signos sueltos: basura de OCR
    num = NUMBERED_HEADING.match(text) if text and len(text) <= 120 else None
    if num and "$" not in text:
        spans = [sp for l in block["lines"] for sp in l["spans"] if sp["text"].strip()]
        bold = bool(spans) and all(sp["flags"] & 16 or "bold" in sp["font"].lower() for sp in spans)
        upper = sum(c.isupper() for c in text) >= .6 * max(1, sum(c.isalpha() for c in text))
        if bold or upper or (sizes and min(sizes) >= body * 1.08):
            return ("## " if num.group(1).count(".") <= 1 else "### ") + text
    if text and sizes and len(text) <= 150 and "$" not in text \
            and len(re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]", text)) >= 3:
        ratio = min(sizes) / body
        if ratio >= 1.45:
            return "## " + text
        if ratio >= 1.18:
            return "### " + text
    return text


def _ink_regions(page, texts) -> list[fitz.Rect]:
    """Figuras dentro de un escaneo con capa de texto: zonas con tinta sin texto encima."""
    import numpy as np
    zoom = .5
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    cell = 8
    rows, cols = gray.shape[0] // cell, gray.shape[1] // cell
    if rows < 4 or cols < 4:
        return []
    ink = (gray[:rows * cell, :cols * cell] < 190).reshape(rows, cell, cols, cell).mean(axis=(1, 3)) >= .04
    covered = np.zeros((rows, cols), bool)
    for box, chars in texts:
        if chars < 20:
            continue                                   # rótulos y basura de OCR dentro de figuras
        x0, y0 = int(max(0, (box.x0 - 3) * zoom // cell)), int(max(0, (box.y0 - 3) * zoom // cell))
        x1, y1 = int(min(cols, (box.x1 + 3) * zoom // cell + 1)), int(min(rows, (box.y1 + 3) * zoom // cell + 1))
        covered[y0:y1, x0:x1] = True
    free = ink & ~covered
    free[:int(rows * .05)] = False
    free[-int(rows * .05) or rows:] = False
    free[:, :int(cols * .05)] = False
    free[:, -int(cols * .05) or cols:] = False
    grown = free.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
        grown |= np.roll(np.roll(free, dy, 0), dx, 1)
    seen = np.zeros_like(grown)
    found = []
    for r0 in range(rows):
        for c0 in range(cols):
            if not grown[r0, c0] or seen[r0, c0]:
                continue
            stack, comp = [(r0, c0)], []
            seen[r0, c0] = True
            while stack:
                r, c = stack.pop()
                comp.append((r, c))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols and grown[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
            rs, cs = [r for r, _ in comp], [c for _, c in comp]
            box = fitz.Rect(min(cs) * cell / zoom, min(rs) * cell / zoom,
                            (max(cs) + 1) * cell / zoom, (max(rs) + 1) * cell / zoom)
            filled = sum(1 for r, c in comp if free[r, c]) * (cell / zoom) ** 2
            if box.get_area() >= .07 * page.rect.get_area() and box.width >= 110 \
                    and box.height >= 80 and filled >= .12 * box.get_area():
                found.append((box + (-6, -6, 6, 6)) & page.rect)
    return found


def _visual_regions(page, blocks=None) -> list[tuple[fitz.Rect, str]]:
    """Figuras de la página. Los fondos y marcos de recuadros de texto (ejemplos,
    derivaciones, definiciones) no son figuras: su contenido se procesa como texto."""
    area = page.rect.get_area()
    regions = []
    texts = [(fitz.Rect(b["bbox"]), sum(len(s["text"].strip()) for l in b["lines"] for s in l["spans"]))
             for b in (blocks or []) if b.get("lines")]
    text_chars = sum(n for _, n in texts)
    scan_background = False
    mat = page.rotation_matrix
    for item in page.get_image_info():
        rect = (fitz.Rect(item["bbox"]) * mat) & page.rect
        if rect.get_area() >= .7 * area and text_chars >= 200:
            scan_background = True                    # página escaneada con texto de OCR previo
            continue
        if rect.width >= 55 and rect.height >= 45 and rect.get_area() >= area * .018:
            regions.append((rect, "image"))
    if scan_background:
        try:
            regions += [(r, "image") for r in _ink_regions(page, texts)]
        except Exception:  # noqa: BLE001
            pass

    def text_in(rect):
        inside = [(box, n) for box, n in texts if _inside(box, rect, .8)]
        return sum(n for _, n in inside), sum(box.get_area() for box, _ in inside)

    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        drawings = []
    keep, checked = [], {}
    for d in drawings:
        r = fitz.Rect(d["rect"])
        if r.get_area() >= area * .06 and text_in(r)[0] >= 200:
            key = tuple(round(v) for v in r)
            if key not in checked:
                inner = [x for x in drawings if x is not d and _inside(fitz.Rect(x["rect"]), r, .9)
                         and not (fitz.Rect(x["rect"]).height <= 2.5 or fitz.Rect(x["rect"]).width <= 2.5)]
                chars, covered = text_in(r)
                grid = any(t.row_count >= 2 and t.col_count >= 2 for t in _tables(page, r))
                checked[key] = not grid and (len(inner) <= 5 or covered >= .4 * r.get_area())
            if checked[key]:
                continue                               # fondo o marco de un recuadro de texto
        keep.append(d)
    try:
        clusters = page.cluster_drawings(drawings=keep) if keep else []
    except (AttributeError, ValueError, TypeError):
        clusters = []
    for box in clusters:
        rect = fitz.Rect(box) & page.rect
        if not (rect.width >= 110 and rect.height >= 65 and area * .055 <= rect.get_area() <= area * .85):
            continue                                   # > 85 %: fondo de página
        chars, covered = text_in(rect)
        paths = sum(1 for d in keep if fitz.Rect(d["rect"]).intersects(rect))
        if ((paths <= 4 and chars > 150) or (chars >= 300 and covered >= .45 * rect.get_area())) \
                and not any(t.row_count >= 2 and t.col_count >= 2 for t in _tables(page, rect)):
            continue                                   # sobre todo texto y sin cuadrícula: no es figura
        regions.append((rect, "drawing"))
    regions.sort(key=lambda r: r[0].get_area(), reverse=True)
    distinct = []
    for rect, kind in regions:
        if not any((rect & old).get_area() / max(1, rect.get_area()) > .72 for old, _ in distinct):
            distinct.append((rect, kind))
    return distinct[:20]


def _fit_clip(rect: fitz.Rect, blocks: list[dict], page_rect: fitz.Rect) -> fitz.Rect:
    """Incluye el pie de figura completo y evita cortar renglones por la mitad."""
    clip = fitz.Rect(rect)
    for b in blocks:
        box = fitz.Rect(b["bbox"])
        if box.x1 < clip.x0 or box.x0 > clip.x1:
            continue
        first = "".join(s["text"] for s in b["lines"][0]["spans"]) if b["lines"] else ""
        if CAPTION.match(first) and (clip.y1 - 15 <= box.y0 <= clip.y1 + 30
                                     or clip.y0 - 30 <= box.y1 <= clip.y0 + 15):
            clip |= box
    for b in blocks:
        for line in b["lines"]:
            box = fitz.Rect(line["bbox"])
            if box.x1 < clip.x0 or box.x0 > clip.x1:
                continue
            if box.y0 < clip.y1 < box.y1:
                clip.y1 = box.y1 if clip.y1 - box.y0 > box.y1 - clip.y1 else box.y0 - .5
            if box.y0 < clip.y0 < box.y1:
                clip.y0 = box.y0 if box.y1 - clip.y0 > clip.y0 - box.y0 else box.y1 + .5
    clip = fitz.Rect(clip.x0 - 4, clip.y0 - 4, clip.x1 + 4, clip.y1 + 4) & page_rect
    return clip if clip.height > 20 and clip.width > 20 else fitz.Rect(rect) & page_rect


def _save_figure(page, clip: fitz.Rect, dest: Path) -> None:
    pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), clip=clip, alpha=False)
    tmp = dest.with_name(dest.name + ".tmp")
    pix.save(str(tmp), output="png")
    os.replace(tmp, dest)


def _tables(page, rect: fitz.Rect) -> list:
    try:
        found = page.find_tables(clip=rect)
    except Exception:  # noqa: BLE001 - la detección de tablas es opcional
        return []
    tables = []
    for table in getattr(found, "tables", []):
        if table.row_count >= 2 and table.col_count >= 2:
            tables.append(table)
    return tables


def _table_markdown(table) -> str:
    try:
        rows = table.extract()
    except Exception:  # noqa: BLE001
        return ""
    def cell(v):
        return " ".join(str(v or "").split()).replace("|", "/")
    rows = [[cell(v) for v in r] for r in rows if any(cell(v) for v in r)]
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    filled = [sum(1 for r in rows if r[c]) for c in range(width)]
    if min(filled) == 0 or sum(1 for f in filled if f) < 2:
        return ""          # cajas de un diagrama, no una tabla
    letters = [c for r in rows for v in r for c in v if c.isalpha()]
    doubled = sum(1 for r in rows for v in r for a, b in zip(v, v[1:]) if a == b and a.isalpha())
    if letters and doubled / len(letters) > .3:
        return ""          # capas de texto sombreado mezcladas letra a letra: no es tabla
    keep = [c for c in range(width) if filled[c] > 1]
    if len(keep) < 2:      # tabla sin líneas internas: renglones de texto, menos tokens
        return "\n".join(" ".join(v for v in r if v) for r in rows)
    rows = [[r[c] for c in keep] for r in rows]
    width = len(keep)
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _inside(inner: fitz.Rect, outer: fitz.Rect, ratio: float = .8) -> bool:
    return (inner & outer).get_area() >= inner.get_area() * ratio > 0


# -------------------------------------------------------------------------- fórmulas
# Fuentes que solo se usan para matemáticas (LaTeX, Word/Cambria Math, STIX, Symbol...).
MATH_FONT = re.compile(
    r"cmmi|cmsy|cmex|cmbsy|cmmib|msam|msbm|eufm|eusm|rsfs|stmary|wasy|esint|lmmath|"
    r"latinmodern-?math|cambria-?math|stix-?math|stixtwo-?math|stixsize|stixintegral|"
    r"stixvariant|stixnonuni|xits-?math|asana-?math|mathjax|"
    r"symbolmt|^symbol|\+symbol|mt-?extra|euclid(symbol|extra|math|fraktur)|mtsy|mtex|txsy|"
    r"txex|txmi|pxsy|pxex|pxmi|ntxmi|ntxsy|ntxex|mathdesign|libertinusmath|firamath|"
    r"newcm.*math|dejavumath|garamondmath|math", re.I)
# Familias con variantes de texto y de matemáticas (STIX, XITS, Cambria): solo cuentan
# las variantes "Math"; STIX-Regular, por ejemplo, es la fuente del texto normal.
# Fuentes cuyo texto extraído no es fiable (símbolos codificados como letras).
UNRELIABLE_FONT = re.compile(r"cmsy|cmex|cmbsy|msam|msbm|symbol|mt-?extra|esint|txsy|txex|"
                             r"pxsy|pxex|ntxsy|ntxex|stmary|wasy", re.I)
MATH_CHARS = re.compile(r"[∑∏∫∮√∂∇∞≈≠≡≤≥∈∉⊂⊆∪∩±∓×÷·→←⇒⇔∝∀∃ΔΣΠΩαβγδεζηθλμνξπρστφχψω]")
EQ_TAG = re.compile(r"^\s*\(\s*([A-Za-z]{0,2}[-.]?\d[\w.\-]{0,9}|[A-Za-z]\.?\d?)\s*\)\s*$")
SIG_CHARS = set("=+-()<>/")
TEX_FONT = re.compile(r"(^|\+)advp|cm(mi|sy|ex|r|bx|ti|bsy|mib)\d|msam|msbm|eufm|eusm|rsfs|txmi|txsy|txex|"
                      r"pxmi|pxsy|pxex|ntxmi|ntxsy|ntxex|stmary|wasy|esint", re.I)
SYMBOL_FONT = re.compile(r"symbol", re.I)
# Codificación Symbol de Adobe (Word la guarda en U+F020–U+F0FF sin mapa Unicode).
_SYM = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖαβχδεφγηιϕκλμνοπθρστυϖωξψζ"))
_SYM.update({"\"": "∀", "$": "∃", "'": "∋", "*": "∗", "-": "−", "@": "≅", "\\": "∴", "^": "⊥",
             "\xa2": "′", "\xa3": "≤", "\xa5": "∞", "\xab": "↔", "\xac": "←", "\xad": "↑",
             "\xae": "→", "\xaf": "↓", "\xb0": "°", "\xb1": "±", "\xb2": "″", "\xb3": "≥",
             "\xb4": "×", "\xb5": "∝", "\xb6": "∂", "\xb7": "•", "\xb8": "÷", "\xb9": "≠",
             "\xba": "≡", "\xbb": "≈", "\xbc": "…", "\xc0": "ℵ", "\xc4": "⊗", "\xc5": "⊕",
             "\xc6": "∅", "\xc7": "∩", "\xc8": "∪", "\xc9": "⊃", "\xca": "⊇", "\xcb": "⊄",
             "\xcc": "⊂", "\xcd": "⊆", "\xce": "∈", "\xcf": "∉", "\xd0": "∠", "\xd1": "∇",
             "\xd5": "∏", "\xd6": "√", "\xd7": "⋅", "\xd8": "¬", "\xd9": "∧", "\xda": "∨",
             "\xdb": "⇔", "\xdc": "⇐", "\xdd": "⇑", "\xde": "⇒", "\xdf": "⇓", "\xe0": "◊",
             "\xe5": "∑", "\xf2": "∫", "\xbd": "|", "\xbe": "—", "\xbf": "↵",
             "\xe1": "〈", "\xf1": "〉", "\x7c": "|", "\x7e": "∼"})
SYMBOL_PUA = {0xF000 + ord(k): v for k, v in _SYM.items()}


ADV_FONT = re.compile(r"(^|\+)advp", re.I)          # fuentes matemáticas de Elsevier
ADV_MAP = str.maketrans({"¼": "=", "ð": "(", "Þ": ")"})


def _fix_symbol(text: str, font: str) -> str:
    """Traduce a Unicode los símbolos de Symbol (área privada) y los seguros de AdvP."""
    if SYMBOL_FONT.search(font) and any(0xF020 <= ord(c) <= 0xF0FF for c in text):
        return text.translate(SYMBOL_PUA)
    if ADV_FONT.search(font):
        return text.translate(ADV_MAP)
    return text


OCR_FONT = re.compile(r"hidden|ocr", re.I)         # capa de texto de un OCR previo


def _is_math_span(span) -> bool:
    if OCR_FONT.search(span["font"]):
        return False
    if MATH_FONT.search(span["font"]):
        return True
    return bool(ADV_FONT.search(span["font"])) and not re.search(r"[A-Za-z]{3,}", span["text"])
BULLETS = "•◦▪▫●○■□➢➤✓✔–—·-\uf0b7\uf0a7\uf076\uf0d8\uf0fc\uf0a8\uf06c\uf06e"


def _latex_dir() -> Path | None:
    candidates = [Path(os.environ["LECTOR_LATEX_MODELS"])] if os.environ.get("LECTOR_LATEX_MODELS") else []
    candidates += [r / "latex_ocr" for r in _bundle_roots()]
    candidates.append(Path(__file__).resolve().parents[1] / "latex_ocr")
    for folder in candidates:
        if all((folder / n).is_file() for n in ("encoder.onnx", "decoder.onnx", "tokenizer.json")):
            return folder
    return None


class LatexOCR:
    """LaTeX-OCR (pix2tex, licencia MIT) exportado a ONNX; decodificación voraz."""
    MEAN, STD = 0.7931 * 255, 0.1738 * 255

    def __init__(self, folder: Path):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        try:
            opts.use_deterministic_compute = True          # mismos resultados en cada ejecución
        except AttributeError:
            pass
        make = lambda n: ort.InferenceSession(str(folder / n), opts,
                                              providers=["CPUExecutionProvider"])
        self.encoder, self.decoder = make("encoder.onnx"), make("decoder.onnx")
        self.enc_in = self.encoder.get_inputs()[0].name
        self.dec_in = [i.name for i in self.decoder.get_inputs()]
        data = json.loads((folder / "tokenizer.json").read_text(encoding="utf-8"))
        special = {a["id"] for a in data.get("added_tokens", [])}
        self.vocab = {i: t for t, i in data["model"]["vocab"].items() if i not in special}

    def _prepare(self, gray):
        from PIL import Image
        np = self.np
        data = gray.astype(np.float32)
        if data.size == 0:
            return None
        lo, hi = data.min(), data.max()
        if hi - lo < 1:
            return None
        data = (data - lo) / (hi - lo) * 255
        ys, xs = np.nonzero(data < 128)
        if not len(ys):
            return None
        data = data[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        img = Image.fromarray(data.astype(np.uint8))
        ratio = max(img.size[0] / 672, img.size[1] / 192)
        if ratio > 1:
            img = img.resize((max(1, int(img.size[0] / ratio)), max(1, int(img.size[1] / ratio))),
                             Image.BILINEAR)
        arr = np.asarray(img, np.float32)
        h, w = arr.shape
        out = np.full((max(32, -(-h // 32) * 32), max(32, -(-w // 32) * 32)), 255, np.float32)
        out[:h, :w] = arr
        return ((out - self.MEAN) / self.STD)[None, None].astype(np.float32)

    def __call__(self, gray, max_tokens: int = 350) -> str:
        """LaTeX reconocido; self.looped indica si se cortó por bucle o por tope."""
        np = self.np
        self.looped = False
        tensor = self._prepare(gray)
        if tensor is None:
            return ""
        context = self.encoder.run(None, {self.enc_in: tensor})[0]
        seq = [1]
        for _ in range(max_tokens):
            x = np.array([seq], np.int64)
            logits = self.decoder.run(None, {self.dec_in[0]: x, self.dec_in[1]: np.ones_like(x, bool),
                                             self.dec_in[2]: context})[0][0, -1]
            token = int(np.argmax(logits))
            if token == 2:
                break
            seq.append(token)
            tail = seq[-24:]
            if len(seq) > 24 and (len(set(tail[-8:])) == 1 or tail[:12] == tail[12:]
                                  or len(set(tail)) <= 3):
                self.looped = True                      # repetición: el modelo alucina
                break
        else:
            self.looped = True
        text = "".join(self.vocab.get(t, "") for t in seq[1:]).replace("Ġ", " ").strip()
        return _tidy_latex(text)


def _tidy_latex(s: str) -> str:
    letter, noletter = "[a-zA-Z]", r"[\W_^\d]"
    new = s
    while True:
        s = new
        new = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, noletter), r"\1\2", s)
        new = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, letter), r"\1\2", new)
        new = re.sub(r"(%s)\s+?(%s)" % (letter, noletter), r"\1\2", new)
        if new == s:
            break
    s = re.sub(r"\{([A-Za-z0-9])\}(?=[_^])", r"\1", s)            # {C}_{L} -> C_{L}
    s = re.sub(r"(\\,|\\!|\\;|\\quad|\s)+$", "", s)
    return s.strip()


_ENGINE: dict = {}


def latex_engine():
    """Motor de fórmulas cargado una sola vez; None si no está disponible."""
    if "ocr" not in _ENGINE:
        folder = _latex_dir()
        try:
            _ENGINE["ocr"] = LatexOCR(folder) if folder else None
            _ENGINE["error"] = "" if folder else "modelos de fórmulas no encontrados"
        except Exception as exc:  # noqa: BLE001
            _ENGINE["ocr"], _ENGINE["error"] = None, str(exc)[:200]
    return _ENGINE["ocr"]


def _signature(text: str):
    return Counter(c for c in text if c.isascii() and (c.isalnum() or c in SIG_CHARS))


def _source_signature(spans) -> Counter:
    return _signature("".join(s["text"] for s in spans if not UNRELIABLE_FONT.search(s["font"])))


def _latex_signature(tex: str) -> Counter:
    return _signature(re.sub(r"\\[a-zA-Z]+", " ", tex))


def _fix_equals(tex: str, src: Counter) -> str:
    missing = src.get("=", 0) - tex.count("=")
    if missing > 0:
        tex = re.sub(r"\\(long)?(right|Right)arrow|\\Longrightarrow|\\longrightarrow",
                     "=", tex, count=missing)
    return tex


def _score(src: Counter, tex: str) -> tuple[float, float]:
    out = _latex_signature(tex)
    if not src:
        return (1.0 if out else 0.0), 1.0
    recall = sum((src & out).values()) / sum(src.values())
    ratio = sum(out.values()) / sum(src.values())
    return recall, ratio


def _balanced(tex: str) -> bool:
    depth = 0
    for i, c in enumerate(tex):
        if c == "{" and (i == 0 or tex[i - 1] != "\\"):
            depth += 1
        elif c == "}" and (i == 0 or tex[i - 1] != "\\"):
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def formula_to_latex(page, rect, spans) -> tuple[str, bool]:
    """(LaTeX, verificado). Reintenta con varias escalas y valida contra el texto del PDF."""
    engine = latex_engine()
    rect = fitz.Rect(rect) & page.rect
    if engine is None or rect.is_empty or rect.width < 3 or rect.height < 3:
        return "", False
    try:
        return _formula_to_latex(engine, page, rect, spans)
    except Exception:  # noqa: BLE001 - una fórmula nunca debe detener el libro
        return "", False


def _formula_to_latex(engine, page, rect, spans) -> tuple[str, bool]:
    import numpy as np
    size = max((s["size"] for s in spans), default=11) or 11
    src = _source_signature(spans)
    chars = sum(len("".join(s["text"].split())) for s in spans)
    max_tokens = int(min(350, 30 + 6 * chars))           # tope proporcional a la fórmula
    best, loops = None, 0
    for px in (28, 24):          # medido: 3.ª y 4.ª escala casi nunca rescatan
        if loops >= 2:
            break                                        # dos bucles: no insistir
        zoom = max(.8, min(6.0, px / size))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect + (-2, -2, 2, 2),
                              colorspace=fitz.csGRAY, alpha=False)
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        tex = _fix_equals(engine(gray, max_tokens), src)
        if getattr(engine, "looped", False):
            loops += 1
            continue
        if not tex or not _balanced(tex):
            continue
        recall, ratio = _score(src, tex)
        value = recall - 2 * max(0.0, ratio - 1.1)
        if best is None or value > best[0]:
            best = (value, tex, recall, ratio)
        if recall >= .95 and ratio <= 1.15:
            break
    if best is None:
        return "", False
    _, tex, recall, ratio = best
    return tex, recall >= .9 and ratio <= 1.3


def _unreliable(text: str, font: str = "") -> bool:
    """Texto ilegible: caracteres privados, de control, o Latin-1 salido de una fuente TeX."""
    tex = bool(TEX_FONT.search(font))
    return any(0xE000 <= ord(c) <= 0xF8FF or ord(c) < 32 or (tex and 0x80 <= ord(c) <= 0x24F)
               for c in text)


def _math_line(line) -> bool:
    spans = [s for s in line["spans"] if s["text"].strip(BULLETS + " \t")]
    total = sum(len(s["text"].strip()) for s in spans)
    if not total or all(OCR_FONT.search(s["font"]) for s in spans):
        return False
    math = sum(len(s["text"].strip()) for s in spans
               if _is_math_span(s) or MATH_CHARS.search(s["text"]))
    prose = " ".join(s["text"] for s in spans if not _is_math_span(s))
    words = len(re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{4,}", prose))
    has_font = any(_is_math_span(s) for s in spans)
    return (has_font and words <= 1) or (math / total >= .3 and words <= 2)


def _rules(page) -> list:
    """Trazos finos horizontales (barras de fracción, raíces) de la página."""
    out = []
    for d in page.get_drawings():
        r = fitz.Rect(d["rect"])
        if r.width >= 3 and r.height <= 2.5:
            out.append(r)
    return out


BIG_FONT = re.compile(r"cmex|cmsy|stixsize|stixintegral|extension|largesymbol|mt-?extra|esint|"
                      r"txex|pxex|ntxex|symbol", re.I)


def _rows(group: dict) -> int:
    rows: list[float] = []
    for line in group["lines"]:
        box = fitz.Rect(line["bbox"])
        size = max((s["size"] for s in line["spans"]), default=10)
        cy = (box.y0 + box.y1) / 2
        if not any(abs(cy - r) < .45 * size for r in rows):
            rows.append(cy)
    return len(rows)


def needs_recognition(group: dict, rules: list) -> bool:
    """Solo van al modelo las fórmulas con estructura 2D real o texto ilegible; las que ya
    son Unicode legible (σ = √V(X), S = {x | x > 0}, pasos de un ejemplo) quedan como texto."""
    spans = group["spans"]
    if any(_unreliable(s["text"], s["font"]) for s in spans):
        return True
    inner = group["rect"] + (0, -1, 0, 1)
    if any(inner.intersects(r) and r.width < inner.width * 1.5 for r in rules):
        return True                                        # barra de fracción o de raíz
    if _rows(group) >= 2:
        text = "".join(s["text"] for s in spans)
        base = sorted(s["size"] for s in spans)[len(spans) // 2]
        if any(c in text for c in "∑∏∫∮⋃⋂") or any(s["size"] > 1.5 * base for s in spans) \
                or any(BIG_FONT.search(s["font"]) for s in spans):
            return True                                    # límites o delimitadores grandes
    return False


def find_formulas(blocks) -> list[dict]:
    """Ecuaciones de display: líneas matemáticas contiguas + número de ecuación."""
    lines, tags = [], []
    for bi, b in enumerate(blocks):
        for li, line in enumerate(b["lines"]):
            text = "".join(s["text"] for s in line["spans"])
            if EQ_TAG.match(text):
                tags.append(((bi, li), fitz.Rect(line["bbox"]), text.strip()))
            elif _math_line(line):
                lines.append(((bi, li), fitz.Rect(line["bbox"]), line))
    lines.sort(key=lambda it: (it[1].y0, it[1].x0))
    groups: list[dict] = []
    for key, rect, line in lines:
        size = max((s["size"] for s in line["spans"]), default=10)
        g = groups[-1] if groups else None
        if g and rect.y0 <= g["rect"].y1 + .9 * size and rect.x0 <= g["rect"].x1 + 40 \
                and rect.x1 >= g["rect"].x0 - 40:
            g["rect"] |= rect
            g["keys"].add(key)
            g["spans"].extend(line["spans"])
            g["lines"].append(line)
        else:
            groups.append({"rect": fitz.Rect(rect), "keys": {key}, "spans": list(line["spans"]),
                           "tag": "", "lines": [line]})
    grouped = set().union(*(g["keys"] for g in groups)) if groups else set()
    for bi, b in enumerate(blocks):                       # límites y exponentes sueltos
        for li, line in enumerate(b["lines"]):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if (bi, li) in grouped or not text or len(text.replace(" ", "")) > 4 \
                    or len(re.findall(r"[A-Za-z]", text)) > 1 or EQ_TAG.match(text):
                continue
            rect = fitz.Rect(line["bbox"])
            for g in groups:
                size = max((s["size"] for s in g["spans"]), default=10)
                zone = g["rect"] + (-2, -.9 * size, 2, .9 * size)
                if zone.intersects(rect):
                    g["rect"] |= rect
                    g["keys"].add((bi, li))
                    g["spans"].extend(line["spans"])
                    g["lines"].append(line)
                    break
    for key, rect, text in tags:
        cy = (rect.y0 + rect.y1) / 2
        for g in groups:
            if g["rect"].y0 - 4 <= cy <= g["rect"].y1 + 4 and rect.x0 >= g["rect"].x1 - 5:
                g["tag"] = text.strip("() ")
                g["keys"].add(key)
                break
    return [g for g in groups if sum(len(s["text"].strip()) for s in g["spans"]) >= 2]


# --------------------------------------------------------------------- checkpoints

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


# ----------------------------------------------------------------------- exportación

MARKER = ".lectorpdfia.json"


def book_folder(output: Path, source: Path) -> Path:
    """Carpeta propia del libro; si otro PDF homónimo ya la usa, añade la huella."""
    label, digest = book_identity(source)
    for name in (_short(label), f"{_short(label)} ({digest})"):
        folder = output / name
        marker = folder / MARKER
        if marker.is_file():
            try:
                if json.loads(marker.read_text(encoding="utf-8")).get("source") == str(source):
                    return folder
            except (ValueError, OSError):
                pass
            continue
        if not folder.exists() or not any(folder.iterdir()):
            return folder
    return output / f"{_short(label)} ({digest})"


def _visual_pdf(rows: list[dict], folder: Path, stem: str) -> list[str]:
    figures = [fig for row in rows for fig in row["figures"] if fig.get("kind") != "ecuacion"]
    produced: list[str] = []
    pdf = fitz.open()
    size = 0

    def flush():
        nonlocal pdf, size
        if len(pdf):
            name = f"{stem} - figuras {len(produced) + 1:03d}.pdf"
            tmp = folder / (name + ".tmp")
            pdf.save(str(tmp), garbage=4, deflate=True)
            os.replace(tmp, folder / name)
            produced.append(name)
        pdf.close()
        pdf = fitz.open()
        size = 0

    for fig in figures:
        path = folder / "imagenes" / fig["file"]
        if not path.exists():
            continue
        pix = fitz.Pixmap(str(path))
        jpeg = pix.tobytes("jpeg", jpg_quality=80)
        if len(pdf) >= 150 or (len(pdf) and size + len(jpeg) > 19_000_000):
            flush()
        ratio = min(523 / pix.width, 760 / pix.height)
        w, h = pix.width * ratio, pix.height * ratio
        page = pdf.new_page(width=595, height=h + 75)       # página ajustada a la figura
        page.insert_text((36, 36), f'{fig["id"]} | Página {fig["page"]}', fontsize=10)
        page.insert_image(fitz.Rect(36, 50, 36 + w, 50 + h), stream=jpeg)
        size += len(jpeg)
    flush()
    return produced


def convert(
    source: str | Path, output: str | Path, *, languages: str = "spa+eng",
    max_pages: int = 200, progress=None, checkpoint_root: Path | None = None,
) -> dict:
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Selecciona un archivo PDF existente.")
    max_pages = max(1, int(max_pages))
    output.mkdir(parents=True, exist_ok=True)
    label, digest = book_identity(source)
    stem = _short(label)
    folder = book_folder(output, source)
    images = folder / "imagenes"
    parts_dir = folder / "partes"
    for d in (folder, images, parts_dir):
        d.mkdir(parents=True, exist_ok=True)
    _write_text(folder / MARKER, json.dumps({"source": str(source)}, ensure_ascii=False))

    stat = source.stat()
    signature = {"v": FORMAT_VERSION, "path": str(source), "size": stat.st_size,
                 "mtime_ns": stat.st_mtime_ns, "folder": str(folder), "lang": languages}
    cache_dir = Path(checkpoint_root) if checkpoint_root else app_data() / "checkpoints"
    cache_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = cache_dir / f"{digest}.jsonl"
    rows = _read_checkpoint(checkpoint, signature)
    with fitz.open(source) as doc:
        if doc.needs_pass:
            raise ValueError("El PDF está protegido por contraseña.")
        total = len(doc)
        if not total:
            raise ValueError("El PDF no contiene páginas.")
        if len(rows) > total:
            rows = []
        if not checkpoint.exists():
            _write_checkpoint(checkpoint, signature, rows)
        check_not_scanned(doc)
        pages = UprightPages(doc)
        info = analyze_document(doc)
        counters = {"fig": 0, "eq": 0}
        for row in rows:
            for fig in row["figures"]:
                counters["eq" if fig.get("kind") == "ecuacion" else "fig"] += 1
        for index in range(len(rows), total):
            if progress:
                progress(index, total)
            try:
                page = pages.page(index)
                row = _process_page(page, index, info, languages, label, stem, images, counters)
            except Exception as exc:  # noqa: BLE001 - se conserva el texto simple de la página
                row = _fallback_row(doc, index, exc)
            _append_checkpoint(checkpoint, row)
            rows.append(row)
    if progress:
        progress(total, total)
    pages.close()
    result = _export(rows, source, folder, label, stem, total, max_pages)
    checkpoint.unlink(missing_ok=True)
    return result


def _bullet_marks(page) -> list:
    """Viñetas dibujadas (triángulos, cuadros, círculos pequeños) o como imagen diminuta."""
    marks = []
    try:
        for d in page.get_drawings():
            r = fitz.Rect(d["rect"])
            if 2 <= r.width <= 12 and 2 <= r.height <= 12 and d.get("fill") is not None:
                marks.append(r)
    except Exception:  # noqa: BLE001
        pass
    for item in page.get_image_info():
        r = fitz.Rect(item["bbox"]) * page.rotation_matrix
        if 2 <= r.width <= 14 and 2 <= r.height <= 14:
            marks.append(r)
    return marks


def _figure_ocr(page, clip, languages: str) -> str:
    """Texto dentro de una figura raster (infografías, tablas escaneadas, gráficas)."""
    if not find_tesseract()[0]:
        return ""
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False)
        text = " ".join(_ocr(pix, languages).split())
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return ""
    if len(re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", text)) < 3:
        return ""                                     # sin texto útil (foto, curva sin rótulos)
    return text[:3000]


def _reading_order(items: list) -> list:
    """Orden de lectura con columnas: entre dos elementos de ancho completo (títulos,
    figuras anchas) se lee primero toda la columna izquierda y luego la derecha."""
    if len(items) < 3:
        return sorted(items, key=lambda it: (round(it[0].y0), it[0].x0))
    x0 = min(r.x0 for r, _ in items)
    x1 = max(r.x1 for r, _ in items)
    mid = (x0 + x1) / 2
    def col(r):
        if r.x1 <= mid + 8:
            return 0
        if r.x0 >= mid - 8:
            return 1
        return -1
    two_columns = sum(1 for r, _ in items if col(r) == 1) >= 2 and \
        sum(1 for r, _ in items if col(r) == 0) >= 2
    ordered = sorted(items, key=lambda it: (round(it[0].y0), it[0].x0))
    if not two_columns:
        return ordered
    out, band = [], []
    for it in ordered:
        if col(it[0]) == -1:
            out += sorted(band, key=lambda b: (col(b[0]), round(b[0].y0), b[0].x0))
            band = []
            out.append(it)
        else:
            band.append(it)
    return out + sorted(band, key=lambda b: (col(b[0]), round(b[0].y0), b[0].x0))


def _merge_close(entries: list[list]) -> list[list]:
    """Une bloques contiguos (renglones de un mismo párrafo o pie partido en bloques)."""
    merged: list[list] = []
    for rect, text in entries:
        if merged:
            prev_rect, prev_text = merged[-1]
            gap = rect.y0 - prev_rect.y1
            overlap = min(rect.x1, prev_rect.x1) - max(rect.x0, prev_rect.x0)
            level = text.split(" ", 1)[0]
            if level in ("##", "###") and prev_text.split(" ", 1)[0] == level and -1 <= gap <= 8 \
                    and overlap > 0 and not NUMBERED_HEADING.match(text[len(level) + 1:]):
                merged[-1] = [prev_rect | rect, prev_text + " " + text[len(level) + 1:]]
                continue                               # título partido en dos renglones
            if prev_text.endswith("-") and text[:1].islower() and -1 <= gap <= 4 and overlap > 0:
                merged[-1] = [prev_rect | rect, prev_text[:-1] + text]
                continue                               # palabra cortada entre dos bloques
            if -1 <= gap <= 3 and overlap > 0 and not text.startswith("#") \
                    and not prev_text.startswith("#") and not text.startswith("$$"):
                merged[-1] = [prev_rect | rect, prev_text + " " + text]
                continue
        merged.append([rect, text])
    return merged


def _group_labels(labels: list[list]) -> list[str]:
    """Texto de un diagrama: quita capas duplicadas y une renglones de una misma caja."""
    boxes: list[list] = []
    seen: list[tuple] = []
    for rect, text in sorted(labels, key=lambda e: (e[0].y0, e[0].x0)):
        if any(t == text and (r & rect).get_area() >= .5 * rect.get_area() for r, t in seen):
            continue                                        # sombra desplazada
        seen.append((rect, text))
        for box in boxes:
            r = box[0]
            if -1 <= rect.y0 - r.y1 <= 4 and min(rect.x1, r.x1) - max(rect.x0, r.x0) > 0 \
                    and box[2] == r.y1:
                box[0], box[1], box[2] = r | rect, box[1] + " " + text, rect.y1
                break
        else:
            boxes.append([fitz.Rect(rect), text, rect.y1])
    boxes.sort(key=lambda b: (round(b[0].y0 / 8), b[0].x0))
    return list(dict.fromkeys(" ".join(b[1].split()) for b in boxes))


def _fallback_row(doc, index: int, exc: Exception) -> dict:
    try:
        text = _clean_text(doc[index].get_text("text"))
    except Exception:  # noqa: BLE001
        text = ""
    note = f"[Página procesada en modo simple por un error: {str(exc)[:120]}]"
    return {"page": index + 1, "text": (text + "\n\n" if text else "") + note, "figures": [],
            "ocr": False, "ocr_error": None, "page_error": str(exc)[:200],
            "tables": 0, "equations": 0, "eq_flagged": 0, "inline": 0}


def _process_page(page, index, info, languages, label, stem, images, counters):
    height = page.rect.height
    blocks = [b for b in page.get_text("dict", sort=True)["blocks"] if b["type"] == 0]
    for b in blocks:
        for l in b["lines"]:
            for sp in l["spans"]:
                sp["text"] = _fix_symbol(sp["text"], sp["font"])
    raw = "".join(s["text"] for b in blocks for l in b["lines"] for s in l["spans"])
    scanned = len(re.findall(r"\w", raw)) < 45 and _image_coverage(page) >= .5
    ocr_error = None
    items: list[tuple] = []
    figures = []
    stats = {"tables": 0, "equations": 0, "eq_flagged": 0, "inline": 0}

    def save(clip, kind="figura"):
        clip = fitz.Rect(clip) & page.rect
        if clip.is_empty or clip.width < 4 or clip.height < 4:
            return ""                                     # recorte fuera de la página
        if kind == "ecuacion":
            counters["eq"] += 1
            n, image_id = counters["eq"], f"Ecuación {counters['eq']} ({label})"
            file_name = f"Ecuacion {n:04d} ({stem}).png"
        else:
            counters["fig"] += 1
            n, image_id = counters["fig"], f"Imagen {counters['fig']} ({label})"
            file_name = f"Imagen {n:04d} ({stem}).png"
        _save_figure(page, clip, images / file_name)
        figures.append({"id": image_id, "file": file_name, "page": index + 1, "kind": kind})
        short = image_id.split(" (", 1)[0]               # "Imagen 27": el libro va en el encabezado
        return f"![{short}](<imagenes/{file_name}>)"

    if scanned:
        text = ""
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            text = _clean_text(_ocr(pix, languages))
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            ocr_error = str(exc)[:300]
            text = f"[OCR pendiente: {ocr_error}]"
        items.append((page.rect, save(page.rect) + ("\n\n" + text if text else "")))
    else:
        formulas = find_formulas(blocks)
        skip = set().union(*(f["keys"] for f in formulas)) if formulas else set()
        rules = _rules(page) if formulas else []
        for f in formulas:
            if not needs_recognition(f, rules):
                lines = sorted(f["lines"], key=lambda l: (l["bbox"][1], l["bbox"][0]))
                text = " ".join(_line_text(l, None, stats).strip() for l in lines)
                text = " ".join(text.split()) + (f" ({f['tag']})" if f["tag"] else "")
                stats["math_text"] = stats.get("math_text", 0) + 1
                items.append((f["rect"], text))
                continue
            lines = sorted(f["lines"], key=lambda l: (l["bbox"][1], l["bbox"][0]))
            size = max((sp["size"] for sp in f["spans"]), default=10)
            readable = not any(_unreliable(sp["text"], sp["font"]) for sp in f["spans"])
            too_big = f["rect"].height > 6 * size or _rows(f) > 5
            tex, ok = ("", False) if too_big else formula_to_latex(page, f["rect"], f["spans"])
            tag = f" \\tag{{{f['tag']}}}" if f["tag"] else ""
            clip = f["rect"] + (-3, -3, 3, 3)
            if tex and ok:
                md = f"$${tex}{tag}$$"
                stats["equations"] += 1
            elif readable:                               # el texto del PDF es mejor que un LaTeX dudoso
                text = " ".join(" ".join(_line_text(l, None, stats).split()) for l in lines)
                stats["eq_flagged"] += 1
                md = text + (f" ({f['tag']})" if f["tag"] else "") + " " + \
                     save(clip, "ecuacion") + " [verificar fórmula]"
            elif tex:
                stats["equations"] += 1
                stats["eq_flagged"] += 1
                md = f"$${tex}{tag}$$ " + save(clip, "ecuacion") + " [verificar fórmula]"
            else:
                stats["eq_flagged"] += 1
                md = save(clip, "ecuacion") + (f" ({f['tag']})" if f["tag"] else "") + \
                     " [fórmula sin convertir]"
            items.append((f["rect"], md))
        formula_rects = [f["rect"] for f in formulas]
        regions = [(r, k) for r, k in _visual_regions(page, blocks)
                   if not any((r & fr).get_area() >= .5 * r.get_area() for fr in formula_rects)]
        used: set[int] = set()
        for rect, kind in regions:
            near = rect + (-28, -6, 28, 28)                # rótulos de ejes y leyendas
            for i, b in enumerate(blocks):
                box = fitz.Rect(b["bbox"])
                text = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
                sizes = [s["size"] for l in b["lines"] for s in l["spans"] if s["text"].strip()]
                numeric = sum(c.isdigit() or c in " .,-−%()" for c in text) / max(1, len(text))
                if i in used or not text or _inside(box, rect) or not _inside(box, near, .95) \
                        or len(text) > (120 if numeric >= .6 else 40) or CAPTION.match(text) \
                        or (sizes and max(sizes) > info["body"] * 1.1):
                    continue
                rect = rect | box
            clip = _fit_clip(rect, blocks, page.rect)
            extra = []
            tables = []
            for table in (_tables(page, rect) if kind == "drawing" else []):
                md = _table_markdown(table)
                if md:
                    tables.append(table)
                    extra.append(md)
                    stats["tables"] += 1
                    tbox = fitz.Rect(table.bbox)
                    for i, b in enumerate(blocks):
                        if _inside(fitz.Rect(b["bbox"]), tbox, .6):
                            used.add(i)
            labels = []
            for i, b in enumerate(blocks):
                if i in used or not _inside(fitz.Rect(b["bbox"]), rect):
                    continue
                first = "".join(s["text"] for s in b["lines"][0]["spans"]) if b["lines"] else ""
                if CAPTION.match(first):
                    continue                              # el pie queda como texto normal
                used.add(i)
                if not tables:
                    t = _block_text(b, height, info["repeated"], info["body"]).lstrip("# ")
                    if t:
                        labels.append([fitz.Rect(b["bbox"]), t])
            labels = _group_labels(labels)
            if labels:
                extra.insert(0, "Texto en la figura: " + " · ".join(labels))
            elif kind == "image" and rect.get_area() >= .04 * page.rect.get_area():
                ocr_text = _figure_ocr(page, clip, languages)
                if ocr_text:
                    stats["fig_ocr"] = stats.get("fig_ocr", 0) + 1
                    extra.insert(0, "Texto en la figura (OCR): " + ocr_text)
            items.append((rect, save(clip) + ("\n\n" + "\n\n".join(extra) if extra else "")))
        recent: deque[str] = deque(maxlen=8)
        entries = []
        edge = _edge_indices([b["bbox"] for b in blocks if b["lines"]], height)
        edge = {i for i, _ in enumerate(b for b in blocks if b["lines"]) if i in edge}
        nonempty = [i for i, b in enumerate(blocks) if b["lines"]]
        edge = {nonempty[k] for k in edge}
        for i, b in enumerate(blocks):
            if i in used:
                continue
            text = _block_text(b, height, info["repeated"], info["body"], skip, i, page, stats,
                               edge=i in edge)
            key = " ".join(text.split()).casefold()        # exacta: "Paso 1" ≠ "Paso 2"
            if not text or (len(key) > 3 and key in recent):
                continue                                  # bloque duplicado (sombra)
            recent.append(key)
            lines = [l for li, l in enumerate(b["lines"]) if (i, li) not in skip]
            rect = fitz.Rect(lines[0]["bbox"]) if lines else fitz.Rect(b["bbox"])
            for l in lines[1:]:
                rect |= fitz.Rect(l["bbox"])
            entries.append([rect, text])
        marks = _bullet_marks(page)
        entries = [list(e) for e in _reading_order([tuple(e) for e in entries])]
        for rect, text in _merge_close(entries):
            first = fitz.Rect(rect.x0, rect.y0, rect.x1, min(rect.y1, rect.y0 + 14))
            if marks and not text.startswith(("#", "-", "•", "$$", "!")) and any(
                    m.x1 <= first.x0 + 2 and first.x0 - m.x1 <= 22 and
                    min(m.y1, first.y1) - max(m.y0, first.y0) > 0 for m in marks):
                text = "- " + text
            items.append((rect, text))
    items = _reading_order(items)
    text = "\n\n".join(t for _, t in items) or "[Página sin texto reconocible]"
    return {"page": index + 1, "text": text, "figures": figures, "ocr": scanned,
            "ocr_error": ocr_error, **stats}


def _export(rows, source, folder, label, stem, total, max_pages) -> dict:
    images = folder / "imagenes"
    parts_dir = folder / "partes"
    original = folder / source.name
    if (not original.exists() or original.stat().st_size != source.stat().st_size) \
            and original.resolve() != source:
        shutil.copy2(source, original)

    def page_block(row, rel=""):
        text = row["text"].replace("](<imagenes/", f"](<{rel}imagenes/")
        return f"[p. {row['page']}]\n\n{text}\n\n"

    parts, names = [], []
    for i in range(0, len(rows), max_pages):
        parts.append(rows[i:i + max_pages])
    for number, batch in enumerate(parts, 1):
        name = f"{stem} - parte {number:03d}.md"
        head = (f"# {label} — parte {number} de {len(parts)}\n\n"
                f"Fuente: {source.name}. Páginas {batch[0]['page']}–{batch[-1]['page']}. "
                f"[p. N] marca el inicio de la página N del PDF.\n\n")
        _write_text(parts_dir / name, head + "".join(page_block(r, "../") for r in batch))
        names.append(name)

    visual = _visual_pdf(rows, folder, stem)
    figures = sum(1 for r in rows for f in r["figures"] if f.get("kind") != "ecuacion")
    equations = sum(r.get("equations", 0) for r in rows)
    flagged = sum(r.get("eq_flagged", 0) for r in rows)
    inline = sum(r.get("inline", 0) for r in rows)
    ocr_pages = sum(r["ocr"] for r in rows)
    toc = []
    for row in rows:
        for level, title in re.findall(r"^(#{2,3}) (.+)$", row["text"], re.M):
            toc.append(f"{'  ' if len(level) == 3 else ''}- p. {row['page']}: {title[:120]}")
    if len(toc) > 250:
        toc = [t for t in toc if not t.startswith("  ")][:250]
    head = [f"# {label}\n\n",
            f"Fuente: `{source.name}` · {total} páginas · {figures} imágenes · "
            f"{sum(r.get('tables', 0) for r in rows)} tablas · {equations} ecuaciones en LaTeX"
            f"{f' ({flagged} por verificar)' if flagged else ''} · {ocr_pages} páginas con OCR.\n\n",
            "Formato: [p. N] marca el inicio de la página N del PDF; ^{} y _{} son superíndice "
            "y subíndice; $...$ y $$...$$ son fórmulas LaTeX ([?] o [verificar fórmula] = "
            f"revisar contra la imagen). `Imagen N` y `Ecuación N` equivalen a `Imagen N ({label})`; "
            "están en la carpeta `imagenes`"]
    head.append(f" y en el anexo visual {', '.join(f'`{v}`' for v in visual)}.\n\n" if visual
                else ".\n\n")
    if toc:
        head += ["## Contenido\n\n", "\n".join(toc), "\n\n"]
    head.append("---\n\n")
    merged = f"{stem} - COMPLETO.md"
    _write_text(folder / merged, "".join(head) + "".join(page_block(r) for r in rows))

    expected_imgs = {f["file"] for r in rows for f in r["figures"]}
    for p in images.glob("*.png"):
        if p.name not in expected_imgs:
            p.unlink(missing_ok=True)
    for p in parts_dir.glob("*.md"):
        if p.name not in names:
            p.unlink(missing_ok=True)
    for p in folder.glob(f"{stem} - figuras *.pdf"):
        if p.name not in visual:
            p.unlink(missing_ok=True)
    return {"source": str(source), "pages": total, "figures": figures,
            "tables": sum(r.get("tables", 0) for r in rows), "ocr_pages": ocr_pages,
            "equations": equations, "equations_flagged": flagged, "inline_math": inline,
            "math_text": sum(r.get("math_text", 0) for r in rows),
            "figure_ocr": sum(r.get("fig_ocr", 0) for r in rows),
            "page_errors": sum(1 for r in rows if r.get("page_error")),
            "ocr_failed": sum(1 for r in rows if r.get("ocr_error")),
            "folder": str(folder), "merged": merged, "parts": names, "visual_pdfs": visual}
