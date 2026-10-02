"""Cola persistente SQLite: un trabajador secuencial y checkpoints por página."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .converter import Cancelled, Shutdown, app_data, convert

AUTO_CANCEL_SECONDS = 30 * 60      # a los 30 min de trabajo activo...
AUTO_CANCEL_PROGRESS = .10         # ...si el avance no supera el 10 %


class AutoCancelled(Cancelled):
    pass


class JobStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data() / "jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, output TEXT NOT NULL,
                languages TEXT NOT NULL, state TEXT NOT NULL, current INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0, message TEXT DEFAULT '', created REAL NOT NULL,
                max_pages INTEGER NOT NULL DEFAULT 200,
                folder TEXT NOT NULL DEFAULT ''
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "max_pages" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN max_pages INTEGER NOT NULL DEFAULT 200")
            if "folder" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
            if "elapsed" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN elapsed REAL NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE jobs ADD COLUMN tick REAL NOT NULL DEFAULT 0")
            db.execute("UPDATE jobs SET state='queued', message='Reanudado al abrir' WHERE state='running'")

    @contextmanager
    def _db(self):
        # Cierra siempre la conexión: en Windows un archivo SQLite abierto
        # queda bloqueado y no puede moverse ni borrarse.
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def add(self, source: Path, output: Path, languages: str, max_pages: int = 200) -> int:
        with self._db() as db:
            cur = db.execute("INSERT INTO jobs(source,output,languages,state,created,max_pages) VALUES(?,?,?,?,?,?)",
                             (str(source.resolve()), str(output.resolve()), languages, "queued", time.time(), max_pages))
            return int(cur.lastrowid)

    def all(self) -> list[dict]:
        with self._db() as db:
            return [dict(x) for x in db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 500")]

    def next(self) -> dict | None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',message='' WHERE id=?", (row["id"],))
                return dict(row)
        return None

    def update(self, job_id: int, *, state=None, current=None, total=None, message=None, folder=None,
               elapsed=None):
        values = {"state": state, "current": current, "total": total, "message": message,
                  "folder": folder, "elapsed": elapsed,
                  "tick": time.time() if elapsed is not None else None}
        values = {k: v for k, v in values.items() if v is not None}
        if not values:
            return
        query = ",".join(f"{k}=?" for k in values)
        with self._db() as db:
            db.execute(f"UPDATE jobs SET {query} WHERE id=?", (*values.values(), job_id))

    def retry(self, job_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE jobs SET state='queued',current=0,message='',elapsed=0 "
                       "WHERE id=? AND state IN ('failed','cancelled','done')", (job_id,))

    def cancel_pending(self, job_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE jobs SET state='cancelled',message='Cancelado' "
                       "WHERE id=? AND state='queued'", (job_id,))


class QueueWorker:
    def __init__(self, store: JobStore):
        self.store = store
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.cancel_id: int | None = None
        self.active_id: int | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def cancel(self, job_id: int):
        if job_id == self.active_id:
            self.cancel_id = job_id
            self.paused.clear()
        else:
            self.store.cancel_pending(job_id)

    @staticmethod
    def _summary(result: dict) -> str:
        text = (f"{result['figures']} imágenes · {result['tables']} tablas · "
                f"{result.get('equations', 0)} ecuaciones LaTeX · {result['ocr_pages']} págs. OCR")
        if result.get("equations_flagged"):
            text += f" · {result['equations_flagged']} fórmulas por verificar"
        if result.get("ocr_failed"):
            text += f" · ATENCIÓN: OCR falló en {result['ocr_failed']} página(s)"
        return text

    def _run(self):
        while not self.stop.is_set():
            if self.paused.is_set():
                self.stop.wait(.3)
                continue
            job = self.store.next()
            if job is None:
                self.stop.wait(.4)
                continue
            job_id = job["id"]
            self.active_id = job_id
            clock = {"active": float(job.get("elapsed") or 0), "last": time.monotonic()}
            self.store.update(job_id, elapsed=clock["active"])

            def progress(current: int, total: int):
                now = time.monotonic()
                clock["active"] += now - clock["last"]
                clock["last"] = now
                if self.paused.is_set():
                    self.store.update(job_id, elapsed=clock["active"])
                while self.paused.is_set() and not self.stop.is_set() and self.cancel_id != job_id:
                    self.stop.wait(.2)
                clock["last"] = time.monotonic()                 # la pausa no cuenta
                if self.stop.is_set():
                    raise Shutdown()
                if self.cancel_id == job_id:
                    raise Cancelled()
                if clock["active"] >= AUTO_CANCEL_SECONDS and total and \
                        current / total <= AUTO_CANCEL_PROGRESS:
                    raise AutoCancelled()
                self.store.update(job_id, current=current, total=total, elapsed=clock["active"])

            try:
                result = convert(job["source"], job["output"], languages=job["languages"],
                                 max_pages=job["max_pages"], progress=progress)
                clock["active"] += time.monotonic() - clock["last"]
                self.store.update(job_id, state="done", current=result["pages"],
                                  total=result["pages"], message=self._summary(result),
                                  folder=result["folder"], elapsed=clock["active"])
            except AutoCancelled:
                self.store.update(job_id, state="cancelled", elapsed=clock["active"],
                                  message=f"Cancelado automáticamente: "
                                          f"{AUTO_CANCEL_SECONDS // 60} min con avance de "
                                          f"{int(AUTO_CANCEL_PROGRESS * 100)} % o menos")
            except Cancelled:
                self.store.update(job_id, state="cancelled", message="Cancelado; puede reintentarse")
            except Shutdown:
                self.store.update(job_id, state="queued", message="Pendiente para reanudar")
            except Exception as exc:
                self.store.update(job_id, state="failed", message=str(exc)[:500],
                                  elapsed=clock["active"] + time.monotonic() - clock["last"])
            finally:
                self.active_id = None
                self.cancel_id = None
