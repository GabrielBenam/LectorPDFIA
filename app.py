"""Interfaz Tk de la cola; conversión en hilo separado."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .converter import app_data
from .jobs import JobStore, QueueWorker


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Lector PDF para IA")
        self.geometry("970x570")
        self.minsize(740, 420)
        self.store = JobStore()
        self.worker = QueueWorker(self.store)
        self.output_var = tk.StringVar(value=str(Path.home() / "Documents" / "Libros para IA"))
        self.lang_var = tk.StringVar(value="spa+eng")
        self.pages_var = tk.IntVar(value=40)
        self.status_var = tk.StringVar(value="Listo. Los archivos de cada libro van a la misma carpeta.")
        self._build()
        self.worker.start()
        self.after(450, self.refresh)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Carpeta única de salida:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Elegir…", command=self.choose_output).grid(row=0, column=2)
        ttk.Label(frame, text="Idiomas OCR:").grid(row=1, column=0, sticky="w", pady=10)
        ttk.Combobox(frame, textvariable=self.lang_var, values=("spa+eng", "spa", "eng"),
                     state="readonly", width=15).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(frame, text="Páginas por parte:").grid(row=1, column=1, sticky="e", padx=(0, 110))
        ttk.Spinbox(frame, from_=1, to=100, width=5, textvariable=self.pages_var).grid(
            row=1, column=1, sticky="e", padx=(0, 38))
        ttk.Button(frame, text="Agregar PDFs a la cola", command=self.add_files).grid(
            row=1, column=2, sticky="e")
        cols = ("id", "book", "state", "progress", "detail")
        self.table = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        headers = {"id": "#", "book": "PDF", "state": "Estado", "progress": "Progreso", "detail": "Detalle"}
        widths = {"id": 42, "book": 250, "state": 90, "progress": 90, "detail": 300}
        for col in cols:
            self.table.heading(col, text=headers[col])
            self.table.column(col, width=widths[col], stretch=col in ("book", "detail"))
        self.table.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(8, 6))
        controls = ttk.Frame(frame)
        controls.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.pause_btn = ttk.Button(controls, text="Pausar cola", command=self.toggle_pause)
        self.pause_btn.pack(side="left")
        ttk.Button(controls, text="Cancelar seleccionado", command=self.cancel).pack(side="left", padx=8)
        ttk.Button(controls, text="Reintentar seleccionado", command=self.retry).pack(side="left")
        ttk.Button(controls, text="Abrir carpeta", command=self.open_output).pack(side="right")
        ttk.Label(frame, textvariable=self.status_var, wraplength=850).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

    def choose_output(self):
        chosen = filedialog.askdirectory(title="Elegir carpeta única para todos los libros")
        if chosen:
            self.output_var.set(chosen)

    def add_files(self):
        files = filedialog.askopenfilenames(title="Seleccionar PDFs", filetypes=[("PDF", "*.pdf")])
        if not files:
            return
        output = Path(self.output_var.get()).expanduser()
        try:
            max_pages = self.pages_var.get()
            if not 1 <= max_pages <= 100:
                raise ValueError("Elige entre 1 y 100 páginas por parte.")
            output.mkdir(parents=True, exist_ok=True)
            for name in files:
                self.store.add(Path(name), output, self.lang_var.get(), max_pages)
        except (OSError, ValueError, tk.TclError) as exc:
            messagebox.showerror("Carpeta de salida", str(exc))
            return
        self.status_var.set(f"{len(files)} PDF(s) añadidos. Puedes agregar más durante la conversión.")
        self.refresh()

    def _selected(self) -> int | None:
        selection = self.table.selection()
        return int(selection[0]) if selection else None

    def toggle_pause(self):
        if self.worker.paused.is_set():
            self.worker.paused.clear()
            self.pause_btn.configure(text="Pausar cola")
        else:
            self.worker.paused.set()
            self.pause_btn.configure(text="Continuar cola")

    def cancel(self):
        job_id = self._selected()
        if job_id is not None:
            self.worker.cancel(job_id)

    def retry(self):
        job_id = self._selected()
        if job_id is not None:
            self.store.retry(job_id)

    def open_output(self):
        from subprocess import Popen
        import os
        import sys
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            Popen(["open", str(path)])
        else:
            Popen(["xdg-open", str(path)])

    def refresh(self):
        selected = self.table.selection()
        rows = self.store.all()
        current = set(self.table.get_children())
        for job in rows:
            key = str(job["id"])
            progress = f"{job['current']}/{job['total']}" if job["total"] else "—"
            values = (job["id"], Path(job["source"]).name, job["state"], progress, job["message"])
            if key in current:
                self.table.item(key, values=values)
            else:
                self.table.insert("", "end", iid=key, values=values)
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        self.after(500, self.refresh)

    def _close(self):
        self.worker.stop.set()
        self.destroy()


def main():
    Application().mainloop()
