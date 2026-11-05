"""Store small local settings encrypted with Windows DPAPI."""

from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from pathlib import Path

from app_paths import user_data_dir


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(data: bytes, protect: bool) -> bytes:
    if not hasattr(ctypes, "windll"):
        raise OSError("Windows DPAPI를 사용할 수 없는 환경입니다.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                                         ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                           ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(data)
    destination = _DataBlob()
    if protect:
        succeeded = crypt32.CryptProtectData(ctypes.byref(source), "Arknights Screen Translator",
                                              None, None, None, 1, ctypes.byref(destination))
    else:
        succeeded = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1,
                                                ctypes.byref(destination))
    _ = source_buffer
    if not succeeded:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)


def settings_path() -> Path:
    return user_data_dir() / "settings.dpapi"


def load_settings() -> dict[str, str]:
    try:
        plain = _crypt(settings_path().read_bytes(), protect=False)
        data = json.loads(plain.decode("utf-8"))
        return {str(key): str(value) for key, value in data.items() if isinstance(value, str)}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict[str, str]) -> None:
    payload = json.dumps(settings, ensure_ascii=False).encode("utf-8")
    encrypted = _crypt(payload, protect=True)
    settings_path().write_bytes(encrypted)
