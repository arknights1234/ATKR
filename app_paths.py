"""Filesystem locations shared by the source app and PyInstaller build."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "ArknightsScreenTranslator"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _writable_file(name: str) -> Path:
    """Prefer the executable folder, falling back when it is read-only (e.g. Program Files)."""
    path = install_dir() / name
    existed = path.exists()
    try:
        with path.open("a", encoding="utf-8"):
            pass
        # Probe only: an empty cache file would hide the older caches from migration.
        if not existed:
            path.unlink()
        return path
    except OSError:
        return user_data_dir() / name


def icon_path() -> Path:
    return Path(__file__).resolve().parent / "icon" / "app.ico"


def overlay_log_path() -> Path:
    return _writable_file("overlay.log")


def translation_cache_path() -> Path:
    return _writable_file("deepl_translation_cache.json")


def runtime_dictionary_path() -> Path:
    return install_dir() / "data" / "runtime_dictionary.sqlite3"


def runtime_dictionary_pointer() -> Path:
    return install_dir() / "data" / "runtime_dictionary.current"


def active_runtime_dictionary_path() -> Path:
    base = runtime_dictionary_path()
    try:
        name = runtime_dictionary_pointer().read_text(encoding="utf-8").strip()
        if name and Path(name).name == name:
            return base.with_name(name)
    except OSError:
        pass
    return base
