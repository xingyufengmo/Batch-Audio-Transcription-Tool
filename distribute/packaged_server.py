from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", runtime_root()))


def configure_paths() -> None:
    root = runtime_root()
    bundled = bundled_root()
    os.environ.setdefault("APP_BASE_DIR", str(root))
    os.environ.setdefault("APP_STATIC_DIR", str(bundled / "static"))
    os.environ.setdefault("APP_CONFIG_FILE", str(root / "config.json"))


def open_browser_later(url: str) -> None:
    def worker() -> None:
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    configure_paths()
    url = "http://127.0.0.1:8000"
    open_browser_later(url)
    uvicorn.run("app:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
