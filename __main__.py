from __future__ import annotations

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="PDF a texto limpio con anexos visuales")
    parser.add_argument("--cli", metavar="PDF", help="Convertir sin abrir la interfaz")
    parser.add_argument("--output", help="Carpeta única de salida")
    parser.add_argument("--lang", default="spa+eng", help="Idiomas de OCR")
    parser.add_argument("--max-pages", type=int, default=200, help="Páginas por parte (1-2000)")
    parser.add_argument("--selftest", action="store_true", help="Autodiagnóstico del paquete")
    parser.add_argument("--gui", action="store_true", help="Con --selftest, prueba también la ventana")
    parser.add_argument("--report", help="Con --selftest o --cli, guarda el resultado en este archivo JSON")
    args = parser.parse_args(argv)
    if sys.stdout is None or sys.stderr is None:   # ejecutable sin consola (--noconsole)
        import os
        sink = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if args.selftest:
        from pathlib import Path
        from .selftest import run
        return run(Path(args.report) if args.report else None, gui=args.gui)
    if args.cli:
        import json
        from pathlib import Path
        from .converter import convert
        result = convert(args.cli, args.output or str(Path.cwd() / "exportacion"),
                         languages=args.lang, max_pages=args.max_pages)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        if sys.stdout is not None:  # con --noconsole stdout es None
            print(text)
        return 0
    from .app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
