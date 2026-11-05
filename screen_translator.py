"""Ctrl+S toggles a Korean translation overlay over the Arknights window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import html
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import traceback
import unicodedata
from PIL import Image, ImageDraw, ImageFont
from difflib import SequenceMatcher
import cv2
import numpy as np

from app_paths import active_runtime_dictionary_path, runtime_dictionary_path, translation_cache_path
from deepl_translate import find_glossary_id, translate
from localization_text import EDGE_SYMBOLS, OCR_NARROW_FORMS, PLACEHOLDER_RE, fill_numbers, fold_text, loose_key, normalize_text
from window_finder import (
    BITMAPINFO,
    BITMAPINFOHEADER,
    DIB_RGB_COLORS,
    Window,
    capture_window_image,
    find_windows,
    recognize_text,
    shutdown_ocr_engine,
)


user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_APP = 0x8000
WM_TRANSLATION_READY = WM_APP + 1
MOD_NOREPEAT = 0x4000
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_S = 0x53
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOZORDER = 0x0004
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
WS_POPUP = 0x80000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
BI_RGB = 0
OVERLAY_TIMER_ID = 1
WORKING_BORDER_COLOR = (30, 144, 255)
WORKING_BORDER_WIDTH = 6
MIN_OCR_SCORE = 0.6
MIN_SINGLE_HANZI_SCORE = 0.98
MIN_FONT_SIZE = 12
LINE_GAP_MIN_BREAK = 8
LINE_GAP_BREAK_RATIO = 2.5
SAME_ROW_MIN_OVERLAP = 0.6
SAME_ROW_MAX_GAP = 0.6
CELL_NEIGHBOUR_MAX_GAP = 2.0
TEMPLATE_VALUE = r"([+-]?\d+(?:\.\d+)?%?)"
SIMILAR_MIN_LENGTH = 15
SIMILAR_MIN_RATIO = 0.9
SIMILAR_MAX_CANDIDATES = 300
SIMILAR_PREFIX_RE = re.compile(r"[㐀-鿿0-9A-Za-z]{4,8}")
LEFT_EDGE_TOLERANCE = 6
LEFT_EDGE_MAX_GAP_RATIO = 2.0
NUMERIC_TAIL_RE = re.compile(r"(?=.*[\d%％])[\d.]*[%％]?[)）,，.。]*")
NUMERIC_TAIL_MIN_PREVIOUS_CHARS = 12
NUMERIC_TAIL_MAX_WIDTH_SHARE = 0.2
ROOM_MIN_ROW_OVERLAP = 0.3
ROOM_CONTRAST = 38
ROOM_CHANGED_SHARE = 0.25
ROOM_MARGIN = 6
QUOTE_PAIRS = (("“", "”"), ("‘", "’"), ("「", "」"), ("『", "』"), ("《", "》"), ("（", "）"), ("(", ")"))
# Bump when request handling changes, so older machine translations are not reused.
CACHE_KEY_PREFIX = "\x01v2:"
GLOSSARY_CACHE_KEY_PREFIX = "\x01v3:"
GLOSSARY_NAME_PREFIX = "arknights_glossary"
DEEPL_CONTEXT = (
    "명일방주(Arknights) 모바일 게임의 UI 문구와 대사. "
    "作战은 작전, 干员은 오퍼레이터, 代理指挥는 대리 지휘."
)
LABEL_VALUE_RE = re.compile(r"(.+[：:])\s*([0-9OoIl/.]+)")

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                            wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                      ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                      ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                      ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL

TRANSLATION_CACHE_PATH = translation_cache_path()
RUNTIME_DICTIONARY_PATH = runtime_dictionary_path()


def load_translation_cache() -> dict[str, str]:
    legacy_cache = Path(__file__).resolve().parent / ".deepl_translation_cache.json"
    old_appdata_cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ArknightsScreenTranslator" / "deepl_translation_cache.json"
    try:
        cache_path = next(
            (candidate for candidate in (TRANSLATION_CACHE_PATH, old_appdata_cache, legacy_cache) if candidate.is_file()),
            TRANSLATION_CACHE_PATH,
        )
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path != TRANSLATION_CACHE_PATH and data:
            TRANSLATION_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {str(key): str(value) for key, value in data.items()}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


TRANSLATION_CACHE = load_translation_cache()
GLOSSARY_IDS: dict[str, str | None] = {}


RUNTIME_LOOKUP_LOCAL = threading.local()
RUNTIME_GLOSSARY_LOCK = threading.Lock()
RUNTIME_GLOSSARY_CACHE_PATH: Path | None = None
RUNTIME_GLOSSARY_CACHE: tuple[re.Pattern[str] | None, dict[str, str]] | None = None
FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def runtime_dictionary_lookup(source_text: str) -> str | None:
    active_path = active_runtime_dictionary_path()
    if not active_path.is_file():
        return None
    connection = getattr(RUNTIME_LOOKUP_LOCAL, "connection", None)
    if connection is None or getattr(RUNTIME_LOOKUP_LOCAL, "path", None) != active_path:
        if connection is not None:
            connection.close()
        connection = sqlite3.connect(active_path.resolve().as_uri() + "?mode=ro", uri=True)
        RUNTIME_LOOKUP_LOCAL.connection = connection
        RUNTIME_LOOKUP_LOCAL.path = active_path
        RUNTIME_LOOKUP_LOCAL.glossary = None
    row = connection.execute(
        "SELECT translation_ko FROM lookup WHERE source_zh=?", (source_text,)
    ).fetchone()
    return row[0].replace("\\n", "\n") if row else None


def official_translation(source_text: str) -> str | None:
    """Exact official string, else one matching apart from spacing, symbols and numbers."""
    exact = runtime_dictionary_lookup(source_text)
    if exact is None:
        narrow_text = source_text.translate(OCR_NARROW_FORMS)
        if narrow_text != source_text:
            exact = runtime_dictionary_lookup(narrow_text)
    if exact is not None:
        return exact
    for opening, closing in QUOTE_PAIRS:
        # OCR easily misses a faint quote mark at the very start or end of a text.
        if source_text.count(opening) > source_text.count(closing):
            exact = runtime_dictionary_lookup(source_text + closing)
        elif source_text.count(closing) > source_text.count(opening):
            exact = runtime_dictionary_lookup(opening + source_text)
        if exact is not None:
            return exact
    connection = getattr(RUNTIME_LOOKUP_LOCAL, "connection", None)
    if connection is None:
        return None
    key = loose_key(source_text)
    if key != source_text and "#" not in key:
        exact = runtime_dictionary_lookup(key)
        if exact is not None:
            return exact
    filled = template_translation(source_text, key, connection)
    if filled is not None:
        return filled
    try:
        row = connection.execute("SELECT template_ko FROM lookup_loose WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:  # Dictionary released before lookup_loose existed.
        row = None
    if row:
        filled = fill_numbers(row[0], source_text)
        if filled is not None:
            return filled
    label_value = LABEL_VALUE_RE.fullmatch(source_text)
    if label_value:
        label = official_translation(label_value.group(1))
        if label is not None:
            return f"{label} {label_value.group(2)}"
    return similar_official_translation(source_text, connection)


def template_translation(source_text: str, key: str, connection: sqlite3.Connection) -> str | None:
    """Official text with value slots, e.g. a skill's {attack@atk_scale:0%}, filled from the screen.

    Values are matched by slot name because Korean often reorders them, and digits written
    into the template itself must equal what the screen shows.
    """
    try:
        rows = connection.execute(
            "SELECT t.source_zh,l.translation_ko FROM lookup_templates t "
            "JOIN lookup l ON l.source_zh=t.source_zh WHERE t.key=?",
            (key,),
        ).fetchall()
    except sqlite3.OperationalError:  # Dictionary released before lookup_templates existed.
        return None
    screen = fold_text(source_text).strip(EDGE_SYMBOLS)
    results = {
        filled for filled in (_fill_template(source, translation, screen) for source, translation in rows)
        if filled is not None
    }
    return results.pop() if len(results) == 1 else None


def _fill_template(source: str, translation: str, screen: str) -> str | None:
    slots: list[str] = []
    pattern = ""
    folded = fold_text(source).strip(EDGE_SYMBOLS)
    position = 0
    for match in PLACEHOLDER_RE.finditer(folded):
        pattern += re.escape(folded[position:match.start()]) + TEMPLATE_VALUE
        slots.append(match.group(0))
        position = match.end()
    pattern += re.escape(folded[position:])
    found = re.fullmatch(pattern, screen)
    if not found:
        return None
    values: dict[str, str] = {}
    for slot, value in zip(slots, found.groups()):
        name = _slot_name(slot)
        if "%" in slot and not value.endswith("%"):
            value += "%"
        if values.setdefault(name, value) != value:
            return None

    def fill(match: re.Match[str]) -> str:
        return values.get(_slot_name(match.group(0)), match.group(0))

    filled = PLACEHOLDER_RE.sub(fill, translation).replace("\\n", "\n")
    return None if PLACEHOLDER_RE.search(filled) else filled


def _slot_name(slot: str) -> str:
    return slot.strip("{}").split(":")[0].strip().casefold()


def similar_official_translation(source_text: str, connection: sqlite3.Connection) -> str | None:
    """Official text for a long OCR line with small misreads, such as a missed colored "(+".

    Candidates must start the same and hold the same digits in the same order. Separators are
    ignored because OCR loses them ("8(+1)" read as "81"), but "6" and "7" stay different, so a
    different level of the same skill never matches.
    """
    if len(source_text) < SIMILAR_MIN_LENGTH:
        return None
    prefix = SIMILAR_PREFIX_RE.match(source_text)
    if not prefix:
        return None
    target = _fold_for_comparison(source_text)
    digits = re.sub(r"\D", "", target)
    rows = connection.execute(
        "SELECT source_zh,translation_ko FROM lookup WHERE source_zh>=? AND source_zh<? LIMIT ?",
        (prefix.group(0), prefix.group(0) + "\U0010ffff", SIMILAR_MAX_CANDIDATES),
    )
    best_ratio = 0.0
    best_translations: set[str] = set()
    for candidate, translation in rows:
        folded = _fold_for_comparison(candidate)
        if re.sub(r"\D", "", folded) != digits:
            continue
        ratio = SequenceMatcher(None, target, folded, autojunk=False).ratio()
        if ratio > best_ratio:
            best_ratio, best_translations = ratio, {translation}
        elif ratio == best_ratio:
            best_translations.add(translation)
    if best_ratio >= SIMILAR_MIN_RATIO and len(best_translations) == 1:
        return next(iter(best_translations))
    return None


def _fold_for_comparison(text: str) -> str:
    return fold_text(text)


def runtime_glossary() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    global RUNTIME_GLOSSARY_CACHE_PATH, RUNTIME_GLOSSARY_CACHE
    active_path = active_runtime_dictionary_path()
    if RUNTIME_GLOSSARY_CACHE_PATH == active_path and RUNTIME_GLOSSARY_CACHE is not None:
        return RUNTIME_GLOSSARY_CACHE
    with RUNTIME_GLOSSARY_LOCK:
        if RUNTIME_GLOSSARY_CACHE_PATH == active_path and RUNTIME_GLOSSARY_CACHE is not None:
            return RUNTIME_GLOSSARY_CACHE
        connection = getattr(RUNTIME_LOOKUP_LOCAL, "connection", None)
        if connection is None or getattr(RUNTIME_LOOKUP_LOCAL, "path", None) != active_path:
            if connection is not None:
                connection.close()
            if not active_path.is_file():
                return None, {}
            connection = sqlite3.connect(active_path.resolve().as_uri() + "?mode=ro", uri=True)
            RUNTIME_LOOKUP_LOCAL.connection = connection
            RUNTIME_LOOKUP_LOCAL.path = active_path
        try:
            terms = dict(connection.execute("SELECT source_zh,translation_ko FROM terms"))
        except sqlite3.OperationalError:
            terms = {}
        if terms:
            alternatives = "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
            cached = (re.compile(alternatives), terms)
        else:
            cached = (None, {})
        RUNTIME_GLOSSARY_CACHE_PATH = active_path
        RUNTIME_GLOSSARY_CACHE = cached
        return cached


def protect_official_terms(source_text: str) -> tuple[str, int]:
    """Build DeepL XML with official Korean terms in ignored tags, so particles still fit."""
    pattern, terms = runtime_glossary()
    if pattern is None:
        return html.escape(source_text, quote=False), 0
    parts: list[str] = []
    position = protected = 0
    for match in pattern.finditer(source_text):
        parts.append(html.escape(source_text[position:match.start()], quote=False))
        parts.append(f"<x>{html.escape(terms[match.group(0)], quote=False)}</x>")
        position = match.end()
        protected += 1
    parts.append(html.escape(source_text[position:], quote=False))
    return "".join(parts), protected


def restore_official_terms(translated_text: str) -> str:
    return html.unescape(re.sub(r"</?x>", "", translated_text))


def filter_ocr_noise(detections: list[dict]) -> list[dict]:
    """Drop icons misread as text, and ticker fragments repeated inside a fuller line."""
    kept = []
    for item in detections:
        score = item.get("score")
        if score is not None:
            hanzi = sum("㐀" <= char <= "鿿" for char in str(item.get("text", "")))
            if score < MIN_OCR_SCORE or (hanzi == 1 and score < MIN_SINGLE_HANZI_SCORE):
                continue
        kept.append(item)

    def is_fragment(item: dict) -> bool:
        text = str(item.get("text", ""))
        bounds = box_bounds(item.get("box", []))
        if bounds is None or not any("㐀" <= char <= "鿿" for char in text):
            return False
        for other in kept:
            other_text = str(other.get("text", ""))
            other_bounds = box_bounds(other.get("box", []))
            if other is item or other_bounds is None or len(other_text) <= len(text) or text not in other_text:
                continue
            overlap = min(bounds[3], other_bounds[3]) - max(bounds[1], other_bounds[1])
            if overlap > 0.5 * min(bounds[3] - bounds[1], other_bounds[3] - other_bounds[1]):
                return True
        return False

    return [item for item in kept if not is_fragment(item)]


def choose_window() -> Window | None:
    windows = find_windows()
    games = [item for item in windows if item.process.casefold() == "arknights.exe"]
    if len(games) == 1:
        print(f"명일방주 창 자동 선택: {games[0].title} / HWND {games[0].handle}")
        return games[0]

    print("명일방주 창을 하나로 특정하지 못해 직접 선택이 필요합니다.")
    for index, item in enumerate(windows, 1):
        width, height = item.size
        print(f"{index}. [{item.category}] {item.title} - {item.process} - {width}×{height}")
    if not windows:
        print("표시 중인 창이 없습니다.")
        return None
    try:
        selected = input("번역할 창 번호 (종료: Enter): ").strip()
        if not selected:
            return None
        return windows[int(selected) - 1]
    except (ValueError, IndexError):
        print("목록에 있는 번호를 입력해 주세요.")
        return None


def client_rect_on_screen(hwnd: wintypes.HWND) -> tuple[int, int, int, int] | None:
    client = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        return None
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, client.right - client.left, client.bottom - client.top


def account_glossary_id(auth_key: str) -> str | None:
    """The account's glossary, looked up once. A failed lookup is retried on the next translation."""
    if auth_key not in GLOSSARY_IDS:
        try:
            GLOSSARY_IDS[auth_key] = find_glossary_id(auth_key, GLOSSARY_NAME_PREFIX, "ZH", "KO")
        except (OSError, ValueError) as error:
            print(f"DeepL 용어집 확인 실패: {error}", flush=True)
            return None
    return GLOSSARY_IDS[auth_key]


def make_translation_job(captured_image: np.ndarray, auth_key: str | None = None) -> tuple[Image.Image, list[dict]]:
    captured = time.perf_counter()
    detections = recognize_text(np.ascontiguousarray(captured_image[:, :, :3]))
    raw_detection_count = len(detections)
    detections = group_wrapped_lines(filter_ocr_noise(detections))
    for detection in detections:
        raw_text = str(detection.get("text", ""))
        clean_text = normalize_text(raw_text)
        if raw_text != clean_text:
            detection["ocr_text"] = raw_text
            detection["text"] = clean_text
    recognized = time.perf_counter()
    print(
        f"처리 시간 - OCR {recognized-captured:.2f}s "
        f"(인식 {raw_detection_count}개, 정리 후 {len(detections)}개)",
        flush=True,
    )
    if not detections:
        return Image.fromarray(cv2.cvtColor(captured_image, cv2.COLOR_BGRA2RGB)), []

    translate_indexes: list[int] = []
    request_texts: list[str] = []
    request_cache_keys: list[str] = []
    dictionary_hits = cache_hits = protected_term_count = 0
    auth_key = (auth_key or os.environ.get("DEEPL_AUTH_KEY", "")).strip()
    glossary_id = None
    glossary_checked = False
    for index, detection in enumerate(detections):
        source_text = detection["text"]
        detection["translation"] = source_text
        if not any("\u3400" <= char <= "\u9fff" for char in source_text):
            continue
        dictionary_translation = official_translation(source_text)
        if dictionary_translation is not None:
            detection["translation"] = dictionary_translation
            detection["translation_source"] = "dictionary"
            dictionary_hits += 1
            continue
        if auth_key and not glossary_checked:
            glossary_id = account_glossary_id(auth_key)
            glossary_checked = True
        cache_key = (GLOSSARY_CACHE_KEY_PREFIX if glossary_id else CACHE_KEY_PREFIX) + source_text
        if cache_key in TRANSLATION_CACHE:
            detection["translation"] = TRANSLATION_CACHE[cache_key]
            detection["translation_source"] = "cache"
            cache_hits += 1
            continue
        # Protecting terms in tags alongside a glossary left Chinese untranslated around the tags.
        protected_text, protected = (source_text, 0) if glossary_id else protect_official_terms(source_text)
        translate_indexes.append(index)
        request_texts.append(protected_text)
        request_cache_keys.append(cache_key)
        protected_term_count += protected
    translation_started = time.perf_counter()
    if translate_indexes:
        try:
            if not auth_key:
                raise RuntimeError("DeepL API 키가 없어 사전에 없는 문구는 원문으로 둡니다.")
            translations = None
            if glossary_id:
                try:
                    translations = translate(
                        request_texts, auth_key, "ZH", "KO", context=DEEPL_CONTEXT, glossary_id=glossary_id,
                    )
                except RuntimeError as error:
                    print(f"DeepL 용어집 사용 실패, 용어집 없이 다시 시도합니다: {error}", flush=True)
                    GLOSSARY_IDS.pop(auth_key, None)
                    glossary_id = None
                    texts = [str(detections[index]["text"]) for index in translate_indexes]
                    request_texts = [protect_official_terms(text)[0] for text in texts]
                    request_cache_keys = [CACHE_KEY_PREFIX + text for text in texts]
            if translations is None:
                translations = translate(
                    request_texts, auth_key, "ZH", "KO",
                    context=DEEPL_CONTEXT, tag_handling="xml", ignore_tags=["x"],
                )
        except RuntimeError as error:
            # Official dictionary results are still worth showing.
            print(f"DeepL 번역 실패: {error}", flush=True)
            translations = []
        for index, result, cache_key in zip(translate_indexes, translations, request_cache_keys):
            translated_text = result.get("text", detections[index]["text"])
            if not glossary_id:
                translated_text = restore_official_terms(translated_text)
            detections[index]["translation"] = translated_text
            detections[index]["translation_source"] = "deepl"
            TRANSLATION_CACHE[cache_key] = translated_text
        if translations:
            try:
                TRANSLATION_CACHE_PATH.write_text(
                    json.dumps(TRANSLATION_CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError as error:
                print(f"번역 캐시 저장 실패: {error}", flush=True)
    translated = time.perf_counter()
    print(
        f"처리 시간 - 사전 {dictionary_hits}개 / 캐시 {cache_hits}개 / "
        f"DeepL {len(translate_indexes)}개 / 공식 용어 보호 {protected_term_count}회 "
        f"({translated-translation_started:.2f}s)",
        flush=True,
    )

    translated_image = render_translated_image(captured_image, detections)
    print(f"처리 시간 - 이미지 합성 {time.perf_counter()-translated:.2f}s", flush=True)
    return translated_image, detections


def box_bounds(box) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) for an OCR polygon or flat x1,y1,x2,y2 box."""
    try:
        values = np.asarray(box, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return None
    if values.size < 4 or values.size % 2 or not np.isfinite(values).all():
        return None
    xs, ys = values[0::2], values[1::2]
    left, top, right, bottom = int(xs.min()), int(ys.min()), int(np.ceil(xs.max())), int(np.ceil(ys.max()))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def join_same_line_fragments(detections: list[dict]) -> list[dict]:
    """OCR sometimes cuts one printed line in two; rejoin close neighbours on the same row."""
    has_hanzi = lambda text: any("㐀" <= char <= "鿿" for char in text)
    positioned = [(box_bounds(item.get("box", [])), item) for item in detections]
    rest = [item for bounds, item in positioned if bounds is None or not has_hanzi(str(item.get("text", "")))]
    lines = sorted(
        ((bounds, item) for bounds, item in positioned
         if bounds is not None and has_hanzi(str(item.get("text", "")))),
        key=lambda pair: pair[0][0],
    )
    joined: list[tuple[tuple[int, int, int, int], dict]] = []
    for bounds, item in lines:
        target = None
        for index, (other, other_item) in enumerate(joined):
            height, other_height = bounds[3] - bounds[1], other[3] - other[1]
            overlap = min(bounds[3], other[3]) - max(bounds[1], other[1])
            gap = bounds[0] - other[2]
            if (overlap >= SAME_ROW_MIN_OVERLAP * min(height, other_height)
                    and 0.6 <= height / other_height <= 1.7
                    and -2 <= gap <= SAME_ROW_MAX_GAP * max(height, other_height)
                    and not _separate_labels(str(other_item["text"]), str(item["text"]))):
                target = index
                break
        if target is None:
            joined.append((bounds, item))
            continue
        other, other_item = joined[target]
        x1, y1 = min(other[0], bounds[0]), min(other[1], bounds[1])
        x2, y2 = max(other[2], bounds[2]), max(other[3], bounds[3])
        scores = [float(entry["score"]) for entry in (other_item, item) if entry.get("score") is not None]
        joined[target] = ((x1, y1, x2, y2), {
            "text": str(other_item["text"]) + str(item["text"]),
            "score": min(scores, default=None),
            "box": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        })
    return [item for _, item in joined] + rest


def _separate_labels(left: str, right: str) -> bool:
    """Two neighbours that are each official text, but not together, are distinct labels."""
    return (official_translation(normalize_text(left)) is not None
            and official_translation(normalize_text(right)) is not None
            and official_translation(normalize_text(left + right)) is None)


def _has_cell_neighbour(
    bounds: tuple[int, int, int, int], positioned: list[tuple[tuple[int, int, int, int], dict]],
) -> bool:
    """A separate text close beside a line marks a table cell or tag row, not wrapped prose.

    Closer pieces are the same line cut by OCR, such as a colored "(+1)", and do not count.
    """
    height = bounds[3] - bounds[1]
    for other, _ in positioned:
        if other == bounds:
            continue
        overlap = min(bounds[3], other[3]) - max(bounds[1], other[1])
        if overlap < SAME_ROW_MIN_OVERLAP * min(height, other[3] - other[1]):
            continue
        gap = max(other[0] - bounds[2], bounds[0] - other[2])
        if SAME_ROW_MAX_GAP * height < gap <= CELL_NEIGHBOUR_MAX_GAP * height:
            return True
    return False


def _join_by_official_text(
    positioned: list[tuple[tuple[int, int, int, int], dict]],
) -> list[tuple[tuple[int, int, int, int], dict]]:
    """Rejoin a line OCR cut at a wide gap, e.g. before "【", when official text confirms it.

    The joined pieces may continue on the next line, so that line is tried with them too.
    """
    lines = sorted(positioned, key=lambda pair: (pair[0][1], pair[0][0]))
    for bounds, item in lines:
        if not any("㐀" <= char <= "鿿" for char in str(item.get("text", ""))):
            continue
        height = bounds[3] - bounds[1]
        run = [(bounds, item)]
        while True:
            last = run[-1][0]
            beside = [
                pair for pair in lines
                if 0 < pair[0][0] - last[2] <= CELL_NEIGHBOUR_MAX_GAP * height
                and min(pair[0][3], last[3]) - max(pair[0][1], last[1]) >= SAME_ROW_MIN_OVERLAP * min(height, pair[0][3] - pair[0][1])
            ]
            if not beside:
                break
            run.append(min(beside, key=lambda pair: pair[0][0]))
        if len(run) < 2:
            continue
        below = [
            pair for pair in lines
            if abs(pair[0][0] - bounds[0]) <= LEFT_EDGE_TOLERANCE and 0 <= pair[0][1] - bounds[3] <= height * 0.7
        ]
        tails = [""]
        if below:
            tails.append(normalize_text(str(min(below, key=lambda pair: pair[0][1])[1].get("text", ""))))
        for stop in range(len(run), 1, -1):
            text = "".join(normalize_text(str(piece.get("text", ""))) for _, piece in run[:stop])
            if any(official_translation(text + tail) is not None for tail in tails):
                x1, y1 = min(b[0] for b, _ in run[:stop]), min(b[1] for b, _ in run[:stop])
                x2, y2 = max(b[2] for b, _ in run[:stop]), max(b[3] for b, _ in run[:stop])
                scores = [float(piece["score"]) for _, piece in run[:stop] if piece.get("score") is not None]
                joined = ((x1, y1, x2, y2), {
                    "text": "".join(str(piece.get("text", "")) for _, piece in run[:stop]),
                    "score": min(scores, default=None),
                    "box": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                })
                used = {id(piece) for _, piece in run[:stop]}
                return _join_by_official_text([pair for pair in lines if id(pair[1]) not in used] + [joined])
    return lines


def _wraps_into_number(
    previous_text: str, previous: tuple[int, int, int, int], tail: tuple[int, int, int, int],
) -> bool:
    """A number cut by a line wrap ("+2" / "0"), told apart from a value under a short label."""
    return (len(previous_text) >= NUMERIC_TAIL_MIN_PREVIOUS_CHARS
            and previous_text[-1] in "0123456789.+-"
            and abs(tail[0] - previous[0]) <= LEFT_EDGE_TOLERANCE
            and tail[2] - tail[0] <= NUMERIC_TAIL_MAX_WIDTH_SHARE * (previous[2] - previous[0]))


def _correct_misread_percent(
    group: list[tuple[tuple[int, int, int, int], dict]],
) -> list[tuple[tuple[int, int, int, int], dict]]:
    """OCR reads a lone wrapped "%" as "0%", turning +35% into +350%. Official text decides."""
    if str(group[-1][1].get("text", "")) != "0%":
        return group
    texts = [normalize_text(str(item.get("text", ""))) for _, item in group[:-1]]
    joined = ["".join(texts[start:]) for start in range(len(texts))]
    if any(runtime_dictionary_lookup(text + "0%") is not None for text in joined):
        return group
    if not any(runtime_dictionary_lookup(text + "%") is not None for text in joined):
        return group
    bounds, item = group[-1]
    return [*group[:-1], (bounds, {**item, "text": "%"})]


def group_wrapped_lines(detections: list[dict]) -> list[dict]:
    """Join vertically wrapped Chinese OCR lines before dictionary/API translation."""
    positioned = []
    unpositioned = []
    for item in join_same_line_fragments(detections):
        bounds = box_bounds(item.get("box", []))
        if bounds is None:
            unpositioned.append(item)
        else:
            positioned.append((bounds, item))
    positioned = _join_by_official_text(positioned)
    positioned.sort(key=lambda pair: (pair[0][1], pair[0][0]))
    table_cells ={id(item) for bounds, item in positioned if _has_cell_neighbour(bounds, positioned)}

    groups: list[list[tuple[tuple[int, int, int, int], dict]]] = []
    for bounds, item in positioned:
        text = str(item.get("text", ""))
        target_group = None
        is_numeric_tail = NUMERIC_TAIL_RE.fullmatch(text) is not None
        if id(item) not in table_cells and (is_numeric_tail or any("\u3400" <= char <= "\u9fff" for char in text)):
            x1, y1, _, _ = bounds
            current_height = max(1, bounds[3] - bounds[1])
            candidates = []
            for group in groups:
                previous_bounds, previous_item = group[-1]
                previous_text = str(previous_item.get("text", ""))
                if is_numeric_tail and not _wraps_into_number(previous_text, previous_bounds, bounds):
                    continue
                px1, py1, _, py2 = previous_bounds
                previous_height = max(1, py2 - py1)
                vertical_gap = y1 - py2
                same_text_column = abs(x1 - px1) <= max(30, int(max(previous_height, current_height) * 1.5))
                similar_line_height = 0.6 <= current_height / previous_height <= 1.7
                close_rows = -int(min(previous_height, current_height) * 0.35) <= vertical_gap <= max(
                    8, int(max(previous_height, current_height) * 0.7)
                )
                prior_is_chinese = any("\u3400" <= char <= "\u9fff" for char in previous_text)
                if (id(previous_item) not in table_cells
                        and prior_is_chinese and same_text_column and similar_line_height and close_rows
                        and len(group) < 8):
                    candidates.append((abs(vertical_gap), group))
            if candidates:
                target_group = min(candidates, key=lambda candidate: candidate[0])[1]
        if target_group is None:
            groups.append([(bounds, item)])
        else:
            target_group.append((bounds, item))

    merged: list[dict] = []
    for group in groups:
        for segment in split_group(group):
            merged.append(merge_lines(segment))
    merged.extend(unpositioned)
    return merged


def merge_lines(lines: list[tuple[tuple[int, int, int, int], dict]]) -> dict:
    if len(lines) == 1:
        return lines[0][1]
    x1 = min(bounds[0] for bounds, _ in lines)
    y1 = min(bounds[1] for bounds, _ in lines)
    x2 = max(bounds[2] for bounds, _ in lines)
    y2 = max(bounds[3] for bounds, _ in lines)
    parts = [str(item.get("text", "")) for _, item in lines]
    return {
        "text": "".join(parts),
        "score": min((float(item["score"]) for _, item in lines if item.get("score") is not None), default=None),
        "box": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        "source_lines": parts,
        "source_boxes": [item.get("box", []) for _, item in lines],
    }


def split_group(
    group: list[tuple[tuple[int, int, int, int], dict]],
) -> list[list[tuple[tuple[int, int, int, int], dict]]]:
    """Separate stacked texts, e.g. an effect line above its description.

    Consecutive lines forming an official string come first, longest match winning. Lines
    with no official string are then split where the line spacing jumps.
    """
    if len(group) < 2:
        return [group]
    group = _correct_misread_percent(group)
    texts = [normalize_text(str(item.get("text", ""))) for _, item in group]
    segments: list[list[tuple[tuple[int, int, int, int], dict]]] = []
    unmatched: list[tuple[tuple[int, int, int, int], dict]] = []
    start = 0
    while start < len(group):
        end = next(
            (stop for stop in range(len(group), start, -1)
             if official_translation("".join(texts[start:stop])) is not None),
            None,
        )
        if end is None:
            unmatched.append(group[start])
            start += 1
            continue
        if unmatched:
            segments.extend(split_by_line_spacing(unmatched))
            unmatched = []
        segments.append(group[start:end])
        start = end
    if unmatched:
        segments.extend(split_by_line_spacing(unmatched))
    return segments


def split_by_line_spacing(
    lines: list[tuple[tuple[int, int, int, int], dict]],
) -> list[list[tuple[tuple[int, int, int, int], dict]]]:
    """Needs three lines or more: two lines give no normal spacing to compare with."""
    if len(lines) < 3:
        return [lines]
    gaps = [lines[i + 1][0][1] - lines[i][0][3] for i in range(len(lines) - 1)]
    limit = max(LINE_GAP_MIN_BREAK, sorted(gaps)[len(gaps) // 2] * LINE_GAP_BREAK_RATIO)
    segments = [[lines[0]]]
    for gap, line in zip(gaps, lines[1:]):
        if gap > limit:
            segments.append([line])
        else:
            segments[-1].append(line)
    return segments


def render_translated_image(source: np.ndarray | Image.Image, detections: list[dict]) -> Image.Image:
    """Composite translations in memory without temporary screenshot/result files."""
    synthesis_started = time.perf_counter()
    if isinstance(source, Image.Image):
        image = np.array(source.convert("RGB"), copy=True)
    elif source.ndim == 3 and source.shape[2] == 4:
        image = cv2.cvtColor(source, cv2.COLOR_BGRA2RGB)
    else:
        image = cv2.cvtColor(source, cv2.COLOR_BGR2RGB)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    render_items: list[tuple[dict, tuple[int, int, int, int], tuple[int, int, int]]] = []
    inpaint_regions: list[tuple[int, int, int, int]] = []
    project_font_dir = Path(__file__).resolve().parent / "font"
    system_font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    font_candidates = [project_font_dir / "NotoSansCJKkr-Medium.otf"]
    font_candidates.extend(sorted(
        path for path in project_font_dir.glob("*")
        if path.suffix.lower() in {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
        and path not in font_candidates
    ))
    font_candidates.extend(system_font_dir / name for name in ("malgun.ttf", "malgunsl.ttf", "gulim.ttc", "arial.ttf"))
    font_path = None
    for candidate in font_candidates:
        if candidate.is_file():
            try:
                ImageFont.truetype(str(candidate), 16)
                font_path = candidate
                break
            except OSError:
                continue
    def get_font(size: int):
        if not font_path:
            return ImageFont.load_default()
        key = (str(font_path), size)
        if key not in FONT_CACHE:
            FONT_CACHE[key] = ImageFont.truetype(str(font_path), size)
        return FONT_CACHE[key]

    for item in detections:
        original = str(item.get("text", "")).strip()
        translated = str(item.get("translation", "")).strip()
        if not translated or translated == original or not any("\u3400" <= char <= "\u9fff" for char in original):
            continue
        bounds = box_bounds(item.get("box", []))
        if not bounds:
            continue
        x1, y1, x2, y2 = bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
        if x2 <= x1 or y2 <= y1:
            continue
        # Skip a single oversized decorative text region. A large union box
        # made by joining normal wrapped dialogue lines must still be translated.
        if not item.get("source_lines") and x2 - x1 > 300 and y2 - y1 > 90:
            continue
        polygon = np.asarray(item.get("box", []), dtype=np.int32)
        if polygon.size == 8:
            polygon = polygon.reshape((-1, 2))
        else:
            polygon = np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.int32)

        margin = 5
        left, top = max(0, x1 - margin), max(0, y1 - margin)
        right, bottom = min(image.shape[1], x2 + margin), min(image.shape[0], y2 + margin)
        inpaint_regions.append((left, top, right, bottom))
        patch = image[top:bottom, left:right]
        local_poly = polygon - np.asarray([left, top], dtype=np.int32)
        inside = np.zeros(patch.shape[:2], dtype=np.uint8)
        cv2.fillPoly(inside, [local_poly], 255)
        ring = cv2.bitwise_not(inside)
        background_pixels = patch[ring > 0]
        if background_pixels.size:
            background = np.median(background_pixels, axis=0)
            source_crop = image[y1:y2, x1:x2].astype(np.int16)
            contrast = np.linalg.norm(source_crop - background.astype(np.int16), axis=2)
            text_mask = (contrast > 38).astype(np.uint8) * 255
            local_inside = inside[y1-top:y2-top, x1-left:x2-left]
            text_mask = cv2.bitwise_and(text_mask, local_inside)
            mask[y1:y2, x1:x2] = cv2.bitwise_or(mask[y1:y2, x1:x2], text_mask)
            luma = float(background[0] * 0.299 + background[1] * 0.587 + background[2] * 0.114)
        else:
            luma = 50
        render_items.append((item, (x1, y1, x2, y2), (luma, 0, 0)))

    mask_ready = time.perf_counter()
    merged_regions = merge_overlapping_regions(inpaint_regions)
    if np.any(mask):
        mask = cv2.dilate(mask, np.ones((3, 3), dtype=np.uint8), iterations=1)
        for left, top, right, bottom in merged_regions:
            region_mask = mask[top:bottom, left:right]
            if np.any(region_mask):
                region = image[top:bottom, left:right]
                image[top:bottom, left:right] = cv2.inpaint(
                    region, region_mask, 3, cv2.INPAINT_TELEA
                )
    inpaint_done = time.perf_counter()

    image_pil = Image.fromarray(image)
    draw = ImageDraw.Draw(image_pil)
    layout_seconds = paint_seconds = 0.0
    all_boxes = [bounds for _, bounds, _ in render_items]
    text_boxes = [bounds for bounds in (box_bounds(item.get("box", [])) for item in detections) if bounds]
    for item, (x1, y1, x2, y2), (luma, _, _) in render_items:
        item_layout_started = time.perf_counter()
        text = str(item["translation"]).strip()
        box_width, box_height = max(20, x2-x1), max(12, y2-y1)
        source_boxes = item.get("source_boxes", [])
        source_heights = []
        for source_box in source_boxes:
            source_bounds = box_bounds(source_box)
            if source_bounds:
                source_heights.append(max(1, source_bounds[3] - source_bounds[1]))
        source_line_height = sorted(source_heights)[len(source_heights) // 2] if source_heights else box_height
        font_size = max(MIN_FONT_SIZE, min(32, round(source_line_height * 0.84)))
        single_source_line = not source_boxes
        max_width = int(box_width * 1.1)
        left_aligned = False
        if single_source_line:
            left_aligned = shares_left_edge((x1, y1, x2, y2), all_boxes)
            max_width = max(max_width, horizontal_room(image, (x1, y1, x2, y2), text_boxes, left_aligned))
            # A one-line label (name, button) reads best as one line: use free space, then shrink.
            full_width = estimate_text_width(text, font_size)
            if full_width > max_width:
                fitted_size = int(font_size * max_width / full_width)
                if fitted_size >= MIN_FONT_SIZE:
                    font_size = fitted_size
        font = get_font(font_size)

        # Wrap before choosing a smaller font. Measuring the full unwrapped Korean
        # sentence here used to shrink it to 10px even when the joined box had room
        # for several readable lines.
        wrapped_text = wrap_text_to_width(text, font, max_width)
        for _ in range(3):
            line_count = wrapped_text.count("\n") + 1
            fitted_size = int((box_height + font_size * 0.25) / (line_count * 1.15))
            if fitted_size >= font_size or not font_path:
                break
            font_size = max(MIN_FONT_SIZE, min(font_size, fitted_size))
            font = get_font(font_size)
            wrapped_text = wrap_text_to_width(text, font, max_width)
        text = wrapped_text
        lines = text.split("\n")
        text_height = len(lines) * font_size * 1.2
        text_x, text_y = float(x1), float(y1)
        align = "left"
        if single_source_line:
            text_y = max(0.0, min(y1 + (box_height - text_height) / 2, image.shape[0] - text_height))
            if not left_aligned:
                # The OCR box hugs the original text, so centering keeps a label or button's position.
                text_width = max(estimate_text_width(line, font_size) for line in lines)
                text_x = max(0.0, x1 + (box_width - text_width) / 2)
                align = "center"
        if luma < 145:
            fill, stroke = (245, 245, 245), (25, 25, 25)
        else:
            fill, stroke = (30, 30, 30), (245, 245, 245)
        layout_done = time.perf_counter()
        draw.multiline_text(
            (text_x, text_y), text, font=font, fill=fill, spacing=0, align=align,
            stroke_width=1, stroke_fill=stroke,
        )
        painted = time.perf_counter()
        layout_seconds += layout_done - item_layout_started
        paint_seconds += painted - layout_done

    pixels = sum((right-left) * (bottom-top) for left, top, right, bottom in merged_regions)
    print(
        f"합성 세부 - 마스크 {mask_ready-synthesis_started:.2f}s / "
        f"부분 인페인팅 {inpaint_done-mask_ready:.2f}s ({len(merged_regions)}영역, "
        f"{pixels:,}/{image.shape[0]*image.shape[1]:,}px) / "
        f"글꼴/배치 {layout_seconds:.2f}s / 글자 그리기 {paint_seconds:.2f}s",
        flush=True,
    )
    return image_pil


def shares_left_edge(
    bounds: tuple[int, int, int, int], others: list[tuple[int, int, int, int]],
) -> bool:
    """True when a text just above or below starts at the same x, as in a left-aligned block."""
    x1, y1, _, y2 = bounds
    for other in others:
        if other == bounds or abs(other[0] - x1) > LEFT_EDGE_TOLERANCE:
            continue
        gap = max(other[1] - y2, y1 - other[3])
        if 0 <= gap <= LEFT_EDGE_MAX_GAP_RATIO * min(y2 - y1, other[3] - other[1]):
            return True
    return False


def horizontal_room(
    image: np.ndarray,
    bounds: tuple[int, int, int, int],
    text_boxes: list[tuple[int, int, int, int]],
    left_aligned: bool,
) -> int:
    """Width a one-line text may use: up to other text or to where the background changes.

    ``image`` has the original text already erased. A left-aligned text grows to the right;
    a centered one grows evenly so that its center stays put.
    """
    x1, y1, x2, y2 = bounds
    left_limit, right_limit = 0, image.shape[1]
    for other in text_boxes:
        if other == bounds:
            continue
        overlap = min(y2, other[3]) - max(y1, other[1])
        if overlap <= ROOM_MIN_ROW_OVERLAP * min(y2 - y1, other[3] - other[1]):
            continue
        if other[0] >= x2:
            right_limit = min(right_limit, other[0])
        elif other[2] <= x1:
            left_limit = max(left_limit, other[2])

    background = np.median(image[y1:y2, x1:x2].reshape(-1, 3), axis=0)

    def free_columns(columns: range) -> int:
        """Columns passed before the background changes; even a thin divider line counts."""
        for count, column in enumerate(columns):
            contrast = np.linalg.norm(image[y1:y2, column].astype(np.int16) - background, axis=1)
            if (contrast > ROOM_CONTRAST).mean() > ROOM_CHANGED_SHARE:
                return max(0, count - ROOM_MARGIN)
        return max(0, len(columns) - ROOM_MARGIN)

    right = free_columns(range(x2, right_limit))
    if left_aligned:
        return x2 - x1 + right
    left = free_columns(range(x1 - 1, left_limit - 1, -1))
    return x2 - x1 + 2 * min(left, right)


def merge_overlapping_regions(
    regions: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Combine overlapping text patches so each image area is inpainted once."""
    merged: list[tuple[int, int, int, int]] = []
    for region in sorted(regions, key=lambda rect: (rect[1], rect[0])):
        x1, y1, x2, y2 = region
        index = 0
        while index < len(merged):
            a1, b1, a2, b2 = merged[index]
            if x1 <= a2 + 2 and x2 + 2 >= a1 and y1 <= b2 + 2 and y2 + 2 >= b1:
                x1, y1, x2, y2 = min(x1, a1), min(y1, b1), max(x2, a2), max(y2, b2)
                merged.pop(index)
                index = 0
            else:
                index += 1
        merged.append((x1, y1, x2, y2))
    return merged


def estimate_text_width(text: str, size: float) -> float:
    """Inexpensive CJK/Korean glyph-width estimate."""
    width = 0.0
    for char in text:
        if char.isspace():
            width += size * 0.36
        elif unicodedata.east_asian_width(char) in {"F", "W", "A"}:
            width += size * 1.02
        else:
            width += size * 0.62
    return width


def wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Wrap with inexpensive CJK/Korean glyph-width estimates."""
    output_lines: list[str] = []
    size = float(getattr(font, "size", 16))

    def width_of(value: str) -> float:
        return estimate_text_width(value, size)

    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        current = ""
        current_width = 0.0
        for word in words:
            word_width = width_of(word)
            if current and current_width + width_of(" ") + word_width <= max_width:
                current += " " + word
                current_width += width_of(" ") + word_width
                continue
            if current:
                output_lines.append(current)
                current = ""
                current_width = 0.0
            if word_width <= max_width:
                current, current_width = word, word_width
                continue
            # A long Korean word or a CJK sequence can break at character boundaries.
            segment = ""
            segment_width = 0.0
            for char in word:
                char_width = width_of(char)
                if segment and segment_width + char_width > max_width:
                    output_lines.append(segment)
                    segment, segment_width = "", 0.0
                segment += char
                segment_width += char_width
            current, current_width = segment, segment_width
        if current:
            output_lines.append(current)
    return "\n".join(line for line in output_lines if line)


class ScreenTranslator:
    """Win32 layered-window overlay; this process does not create a Tcl interpreter."""

    def __init__(self, window: Window):
        self.window = window
        self.hwnd = wintypes.HWND(window.handle)
        self.instance = kernel32.GetModuleHandleW(None)
        self.class_name = f"ArknightsTranslatorOverlay_{os.getpid()}"
        self.window_proc = WNDPROC(self._window_proc)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.visible = False
        self.suspended = False
        self.foreground_when_shown: int | None = None
        self.cancel_after_work = False
        self.closed = False
        self.last_geometry: tuple[int, int, int, int] | None = None
        self.image: Image.Image | None = None
        self.translation_requests: queue.Queue[np.ndarray | None] = queue.Queue()
        self.translation_thread = threading.Thread(
            target=self._translation_worker_loop,
            name="ArknightsTranslationWorker",
            daemon=True,
        )
        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self.window_proc
        wndclass.hInstance = self.instance
        wndclass.lpszClassName = self.class_name
        if not user32.RegisterClassW(ctypes.byref(wndclass)):
            raise ctypes.WinError(ctypes.get_last_error())
        # The game runs elevated. While it has the foreground, Windows silently ignores
        # SetWindowPos(HWND_TOPMOST) from this process, so the flag must be set at creation.
        self.overlay_hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.class_name, "", WS_POPUP, 0, 0, 1, 1, None, None, self.instance, None,
        )
        if not self.overlay_hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.RegisterHotKey(self.overlay_hwnd, 1, MOD_NOREPEAT | MOD_CONTROL, VK_S):
            error = ctypes.get_last_error()
            user32.DestroyWindow(self.overlay_hwnd)
            raise ctypes.WinError(error)
        if not user32.RegisterHotKey(self.overlay_hwnd, 2, MOD_NOREPEAT | MOD_CONTROL | MOD_SHIFT, VK_S):
            error = ctypes.get_last_error()
            user32.UnregisterHotKey(self.overlay_hwnd, 1)
            user32.DestroyWindow(self.overlay_hwnd)
            raise ctypes.WinError(error)
        if not user32.SetTimer(self.overlay_hwnd, OVERLAY_TIMER_ID, 35, None):
            error = ctypes.get_last_error()
            user32.UnregisterHotKey(self.overlay_hwnd, 1)
            user32.UnregisterHotKey(self.overlay_hwnd, 2)
            user32.DestroyWindow(self.overlay_hwnd)
            raise ctypes.WinError(error)
        self.translation_thread.start()
        print("Ctrl+S: 현재 화면 번역 표시 / 숨김 | Ctrl+Shift+S: 종료")

    def mainloop(self) -> None:
        message = wintypes.MSG()
        while not self.closed:
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self.close()

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_HOTKEY:
                if int(wparam) == 2:
                    self.close()
                elif int(wparam) == 1:
                    print("Ctrl+S 입력 감지")
                    self._toggle()
                return 0
            if message == WM_TRANSLATION_READY:
                self._poll()
                return 0
            if message == WM_TIMER:
                self._tick()
                return 0
            if message == WM_CLOSE:
                self.close()
                return 0
            if message == WM_DESTROY:
                self.closed = True
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            print(f"오버레이 창 처리 실패:\n{traceback.format_exc()}", flush=True)
            if message in (WM_HOTKEY, WM_TRANSLATION_READY, WM_TIMER):
                return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _position(self, show: bool) -> bool:
        if not user32.IsWindow(self.hwnd):
            return False
        if not user32.IsWindowVisible(self.hwnd) or user32.IsIconic(self.hwnd):
            if self.visible:
                user32.ShowWindow(self.overlay_hwnd, SW_HIDE)
                self.visible = False
            return False
        rect = client_rect_on_screen(self.hwnd)
        if not rect:
            return False
        left, top, width, height = rect
        if width <= 0 or height <= 0:
            return False
        changed = rect != self.last_geometry
        self.last_geometry = rect
        if changed and self.visible and self.image is not None:
            self._draw(self.image, (width, height))
        if show or self.visible:
            flags = SWP_NOACTIVATE | (SWP_SHOWWINDOW if show else 0)
            user32.SetWindowPos(self.overlay_hwnd, HWND_TOPMOST, left, top, width, height, flags)
            if show:
                user32.ShowWindow(self.overlay_hwnd, SW_SHOWNOACTIVATE)
                self.visible = True
        return True

    def _hide(self) -> None:
        user32.ShowWindow(self.overlay_hwnd, SW_HIDE)
        self.visible = False
        self.suspended = False

    def _toggle(self) -> None:
        if self.busy:
            self.cancel_after_work = True
            self._hide()
            print("번역 중 - 정지 화면을 닫고 결과 표시를 취소합니다.")
            return
        if self.visible or self.suspended:
            self._hide()
            print("번역 오버레이 숨김")
            return
        if not user32.IsWindow(self.hwnd):
            print("게임 창이 닫혀 있습니다.")
            return
        self._bring_game_to_front()
        try:
            frame = capture_window_image(self.window)
        except (OSError, RuntimeError) as error:
            print(f"화면 캡처 실패: {error}")
            return
        self.busy = True
        self.cancel_after_work = False
        print("화면 캡처, OCR, 번역 중...")
        self._show_working_frame(frame)
        self.translation_requests.put(frame)

    def _bring_game_to_front(self) -> None:
        """The capture reads the screen, so the game must be on top before it is taken."""
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_RESTORE)
        if user32.GetForegroundWindow() == self.window.handle:
            return
        if not user32.SetForegroundWindow(self.hwnd):
            # A tapped Alt key lifts Windows' foreground lock for this process.
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            user32.SetForegroundWindow(self.hwnd)
        deadline = time.perf_counter() + 0.5
        while user32.GetForegroundWindow() != self.window.handle and time.perf_counter() < deadline:
            time.sleep(0.01)
        if user32.GetForegroundWindow() != self.window.handle:
            print("게임 창을 맨 앞으로 가져오지 못했습니다. 현재 화면을 그대로 캡처합니다.")
        time.sleep(0.12)  # Let the window that was on top disappear from the screen.

    def _show_working_frame(self, frame: np.ndarray) -> None:
        """Freeze the captured screen at once, with a blue border meaning translation is running."""
        if not self._position(show=False):
            return
        rgb = frame[:, :, [2, 1, 0]].copy()
        border = WORKING_BORDER_WIDTH
        rgb[:border], rgb[-border:] = WORKING_BORDER_COLOR, WORKING_BORDER_COLOR
        rgb[:, :border], rgb[:, -border:] = WORKING_BORDER_COLOR, WORKING_BORDER_COLOR
        width, height = self.last_geometry[2], self.last_geometry[3]
        self._draw(Image.fromarray(rgb), (width, height))
        self._position(show=True)
        self.foreground_when_shown = user32.GetForegroundWindow()

    def _translation_worker_loop(self) -> None:
        while (frame := self.translation_requests.get()) is not None:
            try:
                event = ("result", True, make_translation_job(frame))
            except Exception:
                event = ("result", False, traceback.format_exc())
            self.events.put(event)
            user32.PostMessageW(self.overlay_hwnd, WM_TRANSLATION_READY, 0, 0)
        try:
            shutdown_ocr_engine()
        except Exception as error:
            print(f"OCR 엔진 정리 실패: {error}")

    def _draw(self, image: Image.Image, window_size: tuple[int, int]) -> None:
        if not self.last_geometry:
            return
        width, height = window_size
        resized = image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        rgb = np.asarray(resized, dtype=np.uint8)
        bgra = np.empty((height, width, 4), dtype=np.uint8)
        bgra[:, :, :3] = rgb[:, :, ::-1]
        bgra[:, :, 3] = 255
        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader = BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB,
            bgra.nbytes, 0, 0, 0, 0,
        )
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        memory_dc = None
        bitmap = None
        old_bitmap = None
        try:
            memory_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not memory_dc:
                raise ctypes.WinError(ctypes.get_last_error())
            pixels = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(
                screen_dc, ctypes.byref(bitmap_info), DIB_RGB_COLORS,
                ctypes.byref(pixels), None, 0,
            )
            if not bitmap or not pixels:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(pixels, bgra.ctypes.data, bgra.nbytes)
            old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
            left, top, _, _ = self.last_geometry
            destination = wintypes.POINT(left, top)
            size = wintypes.SIZE(width, height)
            source = wintypes.POINT(0, 0)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            if not user32.UpdateLayeredWindow(
                self.overlay_hwnd, screen_dc, ctypes.byref(destination), ctypes.byref(size),
                memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.image = image
        finally:
            if old_bitmap:
                gdi32.SelectObject(memory_dc, old_bitmap)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)

    def _poll(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            _, succeeded, payload = event
            self.busy = False
            if self.cancel_after_work:
                self.cancel_after_work = False
                print("번역 결과 표시를 취소했습니다.")
            elif not succeeded:
                self._hide()
                print(f"화면 번역 실패: {payload}")
            elif not payload[1]:
                self._hide()
                print("화면에서 번역할 텍스트를 찾지 못했습니다.")
            elif not self._position(show=False):
                self._hide()
                print("게임 창이 보이지 않아 번역을 표시할 수 없습니다.")
            else:
                image, detections = payload
                width, height = self.last_geometry[2], self.last_geometry[3]
                self._draw(image, (width, height))
                # While another app is in front, keep it hidden until the game returns.
                if not self.suspended:
                    self._position(show=True)
                translated_count = sum(item.get("translation") != item.get("text") for item in detections)
                print(f"번역 화면 표시: {translated_count}개 항목")

    def _tick(self) -> None:
        if not user32.IsWindow(self.hwnd):
            print("게임 창이 닫혀 번역기를 종료합니다.")
            self.close()
        elif self.visible or self.suspended:
            self._follow_foreground()
            if self.visible:
                self._position(show=False)

    def _follow_foreground(self) -> None:
        """A topmost overlay would cover other apps, so step aside while one is in front."""
        foreground = user32.GetForegroundWindow()
        if not foreground:  # Transient during Alt+Tab.
            return
        allowed = foreground in (self.window.handle, self.foreground_when_shown)
        if self.visible and not allowed:
            user32.ShowWindow(self.overlay_hwnd, SW_HIDE)
            self.visible = False
            self.suspended = True
        elif self.suspended and allowed:
            self.suspended = False
            self._position(show=True)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        user32.UnregisterHotKey(self.overlay_hwnd, 1)
        user32.UnregisterHotKey(self.overlay_hwnd, 2)
        user32.KillTimer(self.overlay_hwnd, OVERLAY_TIMER_ID)
        if self.translation_thread.is_alive():
            self.translation_requests.put(None)
            if threading.current_thread() is not self.translation_thread:
                self.translation_thread.join()
        if user32.IsWindow(self.overlay_hwnd):
            user32.DestroyWindow(self.overlay_hwnd)


def main() -> int:
    window = choose_window()
    if window is None:
        return 1
    try:
        app = ScreenTranslator(window)
        try:
            app.mainloop()
        except KeyboardInterrupt:
            print("Ctrl+C로 번역기를 종료합니다.")
            app.close()
    except (OSError, RuntimeError) as error:
        print(f"번역 오버레이를 시작하지 못했습니다: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
