"""uv run python -m studio.server [--port 8765] [--dev] [--data <папка>]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .app import create_app


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m studio.server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dev", action="store_true",
                    help="только API, без web/dist (холст – через npm run dev)")
    ap.add_argument("--data", help="папка данных (по умолчанию data/ или STUDIO_DATA)")
    ap.add_argument("--runtime-dsn", default=None,
                    help="база движка для публикации (по умолчанию STUDIO_RUNTIME_DSN; нет – движок не подключён)")
    a = ap.parse_args(argv)
    app = create_app(data_dir=Path(a.data) if a.data else None,
                     dist_dir=Path("__no_dist__") if a.dev else None, runtime_dsn=a.runtime_dsn)
    mode = " (только API; холст – npm run dev → http://localhost:5173)" if a.dev else ""
    print(f"Студия воронок: http://{a.host}:{a.port}/{mode}", flush=True)
    eng = app.state.engine
    print(f"Движок: {eng.where()}" if eng.configured else
          "Движок не подключён (STUDIO_RUNTIME_DSN) – публикация только в хранилище Студии", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
