"""Interfaz Tk de la cola (estilo claro con acento rojo); conversión en hilo separado."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .jobs import JobStore, QueueWorker

try:  # arrastrar y soltar desde el Explorador (opcional)
    from tkinterdnd2 import DND_FILES
    from tkinterdnd2.TkinterDnD import DnDWrapper, _require as _dnd_require
except Exception:  # noqa: BLE001
    DND_FILES, _dnd_require = None, None

    class DnDWrapper:  # type: ignore[no-redef]
        pass

RED, RED_DARK, RED_SOFT = "#E5322D", "#C8231E", "#FDECEB"
BG, CARD, BORDER = "#F4F4F8", "#FFFFFF", "#E2E2EA"
TEXT, MUTED = "#2F2F37", "#74747E"
STATE_LABEL = {"queued": "En cola", "running": "Convirtiendo", "done": "Terminado",
               "failed": "Error", "cancelled": "Cancelado"}
STATE_COLOR = {"queued": MUTED, "running": "#1C6FE0", "done": "#23994A",
               "failed": RED, "cancelled": "#9A6A00"}
LANGS = {"Español + inglés": "spa+eng", "Español": "spa", "Inglés": "eng"}
MAX_PAGES_LIMIT = 2000


def resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / name


class RoundButton(tk.Canvas):
    """Botón de esquinas redondeadas dibujado en un Canvas."""

    def __init__(self, parent, text, command, *, width=200, height=44, bg=RED, fg="white",
                 hover=RED_DARK, outline=None, font=None, parent_bg=BG):
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command, self.colors = command, (bg, hover)
        self.text, self.fg, self.outline, self.font = text, fg, outline, font
        self.bind("<Configure>", lambda e: self._draw(bg))
        self.bind("<Enter>", lambda e: self._draw(hover))
        self.bind("<Leave>", lambda e: self._draw(bg))
        self.bind("<Button-1>", lambda e: self.command())
        self._draw(bg)

    def set_text(self, text):
        self.text = text
        self._draw(self.colors[0])

    def _draw(self, fill):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        x1, y1, x2, y2, r = 1, 1, w - 2, h - 2, min((h - 2) // 2, 10)
        pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
               x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
        self.create_polygon(pts, smooth=True, fill=fill, outline=self.outline or fill, width=1.5)
        self.create_text(w // 2, h // 2, text=self.text, fill=self.fg, font=self.font)


class Application(tk.Tk, DnDWrapper):
    def __init__(self):
        super().__init__()
        self.dnd = False
        if _dnd_require:
            try:
                _dnd_require(self)
                self.dnd = True
            except Exception:  # noqa: BLE001 - sin arrastre, la app sigue funcionando
                self.dnd = False
        self.title("Lector PDF para IA")
        self.geometry("1040x760")
        self.minsize(860, 620)
        self.configure(bg=BG)
        try:
            if sys.platform == "win32":
                self.iconbitmap(default=str(resource("icono.ico")))
        except tk.TclError:
            pass
        self._fonts()
        self._styles()
        self.store = JobStore()
        self.worker = QueueWorker(self.store)
        self.output_var = tk.StringVar(value=str(Path.home() / "Documents" / "Libros para IA"))
        self.lang_var = tk.StringVar(value="Español + inglés")
        self.pages_var = tk.StringVar(value="200")
        self.status_var = tk.StringVar(value="Listo. Cada libro se exporta en su propia carpeta.")
        self.active_var = tk.StringVar(value="")
        self._build()
        self.worker.start()
        self.after(300, self.refresh)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ estilo
    def _fonts(self):
        families = set(tkfont.families(self))
        family = next((f for f in ("Segoe UI", "Helvetica Neue", "DejaVu Sans") if f in families),
                      "TkDefaultFont")
        self.f = {
            "logo": (family, 16, "bold"), "title": (family, 24, "bold"),
            "sub": (family, 11), "btn_big": (family, 14, "bold"), "btn": (family, 10, "bold"),
            "label": (family, 10), "small": (family, 9), "card": (family, 12, "bold"),
        }

    def _styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=CARD, foreground=TEXT, font=self.f["label"])
        s.configure("Card.TFrame", background=CARD)
        s.configure("Card.TLabel", background=CARD, foreground=TEXT)
        s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=self.f["small"])
        s.configure("TEntry", fieldbackground="#FAFAFC", bordercolor=BORDER, lightcolor=BORDER,
                    darkcolor=BORDER, padding=6)
        s.configure("TCombobox", fieldbackground="#FAFAFC", bordercolor=BORDER, padding=5,
                    arrowcolor=RED)
        s.map("TCombobox", fieldbackground=[("readonly", "#FAFAFC")])
        s.configure("TSpinbox", fieldbackground="#FAFAFC", bordercolor=BORDER, padding=5,
                    arrowcolor=RED)
        s.configure("Treeview", background=CARD, fieldbackground=CARD, rowheight=32,
                    bordercolor=BORDER, font=self.f["label"])
        s.configure("Treeview.Heading", background="#F0F0F5", foreground=MUTED,
                    font=self.f["btn"], relief="flat", padding=6)
        s.map("Treeview", background=[("selected", RED_SOFT)], foreground=[("selected", TEXT)])
        s.map("Treeview.Heading", background=[("active", "#E8E8EF")])
        s.configure("Red.Horizontal.TProgressbar", troughcolor="#EDEDF2", background=RED,
                    bordercolor="#EDEDF2", lightcolor=RED, darkcolor=RED, thickness=8)

    def _card(self, parent, **pack):
        outer = tk.Frame(parent, bg=BORDER)
        outer.pack(**pack)
        inner = tk.Frame(outer, bg=CARD, padx=18, pady=14)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

    # ----------------------------------------------------------------- interfaz
    def _build(self):
        header = tk.Frame(self, bg=CARD, height=58)
        header.pack(fill="x")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        logo = tk.Frame(header, bg=CARD)
        logo.pack(side="left", padx=24, pady=14)
        for text, color in (("Lector", TEXT), ("PDF", RED), ("para IA", TEXT)):
            tk.Label(logo, text=text, font=self.f["logo"], fg=color, bg=CARD, padx=0,
                     bd=0).pack(side="left", padx=(0, 5))
        tk.Label(header, text="Todo se procesa en tu equipo · sin conexión", font=self.f["small"],
                 fg=MUTED, bg=CARD).pack(side="right", padx=24)

        tk.Label(self, textvariable=self.status_var, font=self.f["small"], fg=MUTED, bg=BG,
                 anchor="w", padx=30, pady=6).pack(fill="x", side="bottom")

        body = tk.Frame(self, bg=BG, padx=28, pady=10)
        body.pack(fill="both", expand=True)

        self.hero = tk.Frame(body, bg=BG, pady=10)
        self.hero.pack(fill="x")
        tk.Label(self.hero, text="Convierte libros PDF en texto para IA", font=self.f["title"],
                 fg=TEXT, bg=BG).pack()
        tk.Label(self.hero, text="Texto limpio, fórmulas en LaTeX, tablas e imágenes en una carpeta por "
                 "libro, listo para Claude, Gemini, ChatGPT o NotebookLM.", font=self.f["sub"],
                 fg=MUTED, bg=BG).pack(pady=(4, 14))
        RoundButton(self.hero, "Seleccionar archivos PDF", self.add_files, width=330, height=62,
                    font=self.f["btn_big"]).pack()
        self.drop_hint = tk.Label(self.hero, font=self.f["label"], fg=MUTED, bg=BG,
                                  text="o arrastra los PDF a esta ventana" if self.dnd
                                  else "Puedes agregar más PDF mientras convierte")
        self.drop_hint.pack(pady=(8, 4))

        options = self._card(body, fill="x", pady=(8, 10))
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Carpeta de salida", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(options, textvariable=self.output_var).grid(row=0, column=1, sticky="ew")
        RoundButton(options, "Cambiar", self.choose_output, width=96, height=34, bg=CARD,
                    fg=RED, hover=RED_SOFT, outline=RED, font=self.f["btn"],
                    parent_bg=CARD).grid(row=0, column=2, padx=(10, 0))
        row = ttk.Frame(options, style="Card.TFrame")
        row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(row, text="Idiomas del OCR", style="Card.TLabel").pack(side="left")
        ttk.Combobox(row, textvariable=self.lang_var, values=list(LANGS), state="readonly",
                     width=17).pack(side="left", padx=(10, 28))
        ttk.Label(row, text="Páginas por parte", style="Card.TLabel").pack(side="left")
        ttk.Spinbox(row, from_=1, to=MAX_PAGES_LIMIT, increment=50, width=7,
                    textvariable=self.pages_var).pack(side="left", padx=10)
        ttk.Label(row, text=f"(1 a {MAX_PAGES_LIMIT}; además se genera un archivo COMPLETO)",
                  style="Muted.TLabel").pack(side="left")

        queue = self._card(body, fill="both", expand=True, pady=(0, 6))
        top = ttk.Frame(queue, style="Card.TFrame")
        top.pack(fill="x")
        tk.Label(top, text="Cola de conversión", font=self.f["card"], fg=TEXT, bg=CARD).pack(
            side="left")
        tk.Label(top, textvariable=self.active_var, font=self.f["small"], fg=MUTED,
                 bg=CARD).pack(side="right")
        self.progress = ttk.Progressbar(queue, style="Red.Horizontal.TProgressbar",
                                        mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 8))

        controls = ttk.Frame(queue, style="Card.TFrame")
        controls.pack(fill="x", side="bottom", pady=(12, 0))
        small = dict(height=36, bg=CARD, fg=TEXT, hover="#F0F0F5", outline=BORDER,
                     font=self.f["btn"], parent_bg=CARD)
        self.pause_btn = RoundButton(controls, "Pausar cola", self.toggle_pause, width=130, **small)
        self.pause_btn.pack(side="left")
        RoundButton(controls, "Cancelar", self.cancel, width=110, **small).pack(side="left", padx=8)
        RoundButton(controls, "Reintentar", self.retry, width=110, **small).pack(side="left")
        RoundButton(controls, "Abrir carpeta del libro", self.open_output, width=210, height=36,
                    font=self.f["btn"], parent_bg=CARD).pack(side="right")

        table_frame = ttk.Frame(queue, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True)
        cols = ("book", "state", "progress", "time", "detail")
        self.table = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        for col, head, width, stretch in (("book", "PDF", 270, True), ("state", "Estado", 105, False),
                                          ("progress", "Progreso", 140, False),
                                          ("time", "Tiempo", 80, False),
                                          ("detail", "Detalle", 300, True)):
            self.table.heading(col, text=head, anchor="w")
            self.table.column(col, width=width, stretch=stretch, anchor="w")
        for state, color in STATE_COLOR.items():
            self.table.tag_configure(state, foreground=color)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.bind("<Double-1>", lambda e: self.open_output())

        if self.dnd:
            for widget in (self, self.hero, body, self.table):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<DropEnter>>", self._drop_enter)
                    widget.dnd_bind("<<DropLeave>>", self._drop_leave)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                except (tk.TclError, AttributeError):
                    pass

    # ----------------------------------------------------------------- acciones
    def _drop_enter(self, event):
        self.hero.configure(bg=RED_SOFT)
        self.drop_hint.configure(bg=RED_SOFT, text="Suelta los PDF para agregarlos a la cola")
        return event.action

    def _drop_leave(self, event):
        self.hero.configure(bg=BG)
        self.drop_hint.configure(bg=BG, text="o arrastra los PDF a esta ventana")
        return event.action

    def _on_drop(self, event):
        self._drop_leave(event)
        files = [f for f in self.tk.splitlist(event.data) if f.lower().endswith(".pdf")]
        if files:
            self._enqueue(files)
        else:
            self.status_var.set("Solo se aceptan archivos PDF.")
        return event.action

    def choose_output(self):
        chosen = filedialog.askdirectory(title="Elegir la carpeta donde se crearán las carpetas "
                                               "de cada libro")
        if chosen:
            self.output_var.set(chosen)

    def add_files(self):
        files = filedialog.askopenfilenames(title="Seleccionar PDFs", filetypes=[("PDF", "*.pdf")])
        if files:
            self._enqueue(files)

    def _enqueue(self, files):
        output = Path(self.output_var.get()).expanduser()
        try:
            max_pages = int(str(self.pages_var.get()).strip())
            if not 1 <= max_pages <= MAX_PAGES_LIMIT:
                raise ValueError
        except ValueError:
            messagebox.showerror("Páginas por parte",
                                 f"Escribe un número entero entre 1 y {MAX_PAGES_LIMIT}.")
            return
        try:
            output.mkdir(parents=True, exist_ok=True)
            for name in files:
                self.store.add(Path(name), output, LANGS[self.lang_var.get()], max_pages)
        except OSError as exc:
            messagebox.showerror("Carpeta de salida", str(exc))
            return
        self.status_var.set(f"{len(files)} PDF agregado(s) a la cola. Puedes seguir agregando.")
        self.refresh(schedule=False)

    def _selected_job(self) -> dict | None:
        selection = self.table.selection()
        if not selection:
            return None
        return next((j for j in self.store.all() if str(j["id"]) == selection[0]), None)

    def toggle_pause(self):
        if self.worker.paused.is_set():
            self.worker.paused.clear()
            self.pause_btn.set_text("Pausar cola")
        else:
            self.worker.paused.set()
            self.pause_btn.set_text("Continuar cola")

    def cancel(self):
        job = self._selected_job()
        if job:
            self.worker.cancel(job["id"])

    def retry(self):
        job = self._selected_job()
        if job:
            self.store.retry(job["id"])

    def open_output(self):
        job = self._selected_job()
        path = Path(job["folder"]) if job and job.get("folder") else \
            Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def refresh(self, schedule=True):
        try:
            rows = self.store.all()
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"No se pudo leer la cola: {exc}")
            rows = []
        current = set(self.table.get_children())
        active = None
        for job in reversed(rows):          # los más antiguos arriba
            key = str(job["id"])
            pct = int(100 * job["current"] / job["total"]) if job["total"] else 0
            progress = f"{pct}%  ({job['current']}/{job['total']})" if job["total"] else "—"
            seconds = float(job.get("elapsed") or 0)
            if job["state"] == "running" and not self.worker.paused.is_set() and job.get("tick"):
                seconds += max(0.0, time.time() - job["tick"])
            clock = "—" if not seconds else (
                f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d}"
                if seconds >= 3600 else f"{int(seconds // 60)}:{int(seconds % 60):02d} min")
            values = (Path(job["source"]).name, STATE_LABEL.get(job["state"], job["state"]),
                      progress, clock, job["message"])
            if key in current:
                self.table.item(key, values=values, tags=(job["state"],))
            else:
                self.table.insert("", "end", iid=key, values=values, tags=(job["state"],))
            if job["state"] == "running":
                active = (job, pct)
        if active:
            job, pct = active
            self.progress["value"] = pct
            self.active_var.set(f"Convirtiendo {Path(job['source']).name[:55]} · {pct}% · se cancela "
                                f"solo si a los 30 min no pasa del 10 %")
        else:
            self.progress["value"] = 0
            self.active_var.set("En pausa" if self.worker.paused.is_set() else "Sin trabajos activos")
        if schedule:
            self.after(500, self.refresh)

    def _close(self):
        self.worker.stop.set()
        self.destroy()


def main():
    Application().mainloop()
