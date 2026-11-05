"""Find visible Arknights or Android emulator windows on Windows."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import struct
import time
from dataclasses import dataclass
import numpy as np


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.SetProcessDPIAware()
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
_OCR_ENGINE = None


def shutdown_ocr_engine() -> None:
    """Release PaddleX resources on the same persistent worker that created them."""
    global _OCR_ENGINE
    engine, _OCR_ENGINE = _OCR_ENGINE, None
    if engine is not None:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


@dataclass(frozen=True)
class Window:
    handle: int
    title: str
    process: str
    rect: tuple[int, int, int, int]
    category: str

    @property
    def size(self) -> tuple[int, int]:
        return self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]


def _process_name(pid: int) -> str:
    """Return the executable path when Windows allows querying the process."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "(접근 불가)"
    try:
        capacity = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(capacity)):
            return "(확인 불가)"
        return path.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


def _category(title: str, process: str) -> str:
    executable = process.casefold()
    if executable == "arknights.exe":
        return "게임 실행 파일"
    if executable == "games.exe":
        return "런처"
    text = f"{title} {process}".casefold()
    if any(word in text for word in ("arknights", "明日方舟", "명일방주")):
        return "게임 이름 일치"
    if any(word in text for word in ("mumu", "ldplayer", "雷电", "bluestacks", "nox", "夜神", "逍遥", "memu")):
        return "에뮬레이터 후보"
    return "기타 창"


def find_windows() -> list[Window]:
    """Enumerate visible top-level windows with a non-empty title."""
    found: list[Window] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _param: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = _process_name(pid.value)
        title = title_buffer.value.strip()
        # Games.exe is the game launcher in the observed client; it creates a
        # small window with the same title, so omit it regardless of resolution.
        if process.casefold() == "games.exe":
            return True
        found.append(Window(
            int(hwnd), title, process,
            (rect.left, rect.top, rect.right, rect.bottom),
            _category(title, process),
        ))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    # Surface likely targets first, while retaining all windows for emulator titles
    # that do not contain a recognizable brand name.
    order = {"게임 실행 파일": 0, "게임 이름 일치": 1, "에뮬레이터 후보": 2, "기타 창": 3}
    return sorted(found, key=lambda item: (order[item.category], item.title.casefold()))


def capture_window_image(window: Window) -> np.ndarray:
    """Capture the visible client area into a BGRA numpy array without disk I/O."""
    hwnd = wintypes.HWND(window.handle)
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        raise RuntimeError("대상 창이 숨겨져 있거나 최소화되어 있습니다.")

    client = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(client)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = client.right - client.left, client.bottom - client.top
    if width <= 0 or height <= 0:
        raise RuntimeError("게임 창의 클라이언트 영역 크기가 올바르지 않습니다.")

    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise ctypes.WinError(ctypes.get_last_error())
    memory_dc = bitmap = old_bitmap = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not memory_dc or not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, origin.x, origin.y, SRCCOPY):
            raise ctypes.WinError(ctypes.get_last_error())

        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0,
            width * height * 4, 0, 0, 0, 0,
        )
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi32.SelectObject(memory_dc, old_bitmap)
        old_bitmap = None
        rows = gdi32.GetDIBits(screen_dc, bitmap, 0, height, pixels, ctypes.byref(info), DIB_RGB_COLORS)
        if rows != height:
            raise ctypes.WinError(ctypes.get_last_error())

        return np.frombuffer(pixels.raw, dtype=np.uint8).reshape(height, width, 4).copy()
    finally:
        if old_bitmap:
            gdi32.SelectObject(memory_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def capture_window(window: Window, output: Path) -> tuple[int, int]:
    """Capture the visible client area and save a 32-bit BMP for the CLI utility."""
    image = capture_window_image(window)
    height, width = image.shape[:2]
    output.parent.mkdir(parents=True, exist_ok=True)
    pixel_data = image.tobytes()
    file_header = struct.pack("<2sIHHI", b"BM", 54 + len(pixel_data), 0, 0, 54)
    dib_header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0,
                             len(pixel_data), 2835, 2835, 0, 0)
    output.write_bytes(file_header + dib_header + pixel_data)
    return width, height


def recognize_text(image: Path | str | np.ndarray) -> list[dict]:
    """Run local Simplified Chinese OCR on an image path or in-memory BGR array."""
    global _OCR_ENGINE
    try:
        from paddleocr import PaddleOCR
    except ImportError as error:
        raise RuntimeError(
            "PaddleOCR가 설치되어 있지 않습니다. PaddlePaddle과 PaddleOCR 3.x를 "
            "설치한 뒤 다시 실행해 주세요. 현재 Python은 "
            f"{sys.version_info.major}.{sys.version_info.minor}입니다."
        ) from error

    if _OCR_ENGINE is None:
        _OCR_ENGINE = PaddleOCR(
            lang="ch",
            text_detection_model_name="PP-OCRv6_medium_det",
            text_recognition_model_name="PP-OCRv6_medium_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    ocr_input = str(image) if isinstance(image, (str, Path)) else image
    results = _OCR_ENGINE.predict(ocr_input)
    detections: list[dict] = []
    for result in results:
        payload = result.json
        if callable(payload):
            payload = payload()
        if isinstance(payload, str):
            payload = json.loads(payload)
        data = payload.get("res", payload) if isinstance(payload, dict) else {}
        texts = data.get("rec_texts", [])
        scores = data.get("rec_scores", [])
        boxes = data.get("rec_polys", data.get("rec_boxes", []))
        for index, text in enumerate(texts):
            text = str(text).strip()
            if not text:
                continue
            box = boxes[index] if index < len(boxes) else []
            if hasattr(box, "tolist"):
                box = box.tolist()
            score = float(scores[index]) if index < len(scores) else None
            detections.append({"text": text, "score": score, "box": box})
    return detections


def _watch(window: Window) -> None:
    hwnd = wintypes.HWND(window.handle)
    print("\n추적 중입니다. Ctrl+C를 누르면 종료합니다.")
    previous = None
    try:
        while user32.IsWindow(hwnd):
            rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                state = (bool(user32.IsWindowVisible(hwnd)), rect.left, rect.top, rect.right, rect.bottom)
                if state != previous:
                    visible, left, top, right, bottom = state
                    status = "표시" if visible else "숨김"
                    print(f"\r{status} | 위치 ({left}, {top}) | 크기 {right-left}×{bottom-top}     ", end="", flush=True)
                    previous = state
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n추적을 종료했습니다.")
    if not user32.IsWindow(hwnd):
        print("대상 창이 닫혔습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="명일방주 또는 에뮬레이터 창 찾기 (Windows)")
    parser.add_argument("--list", action="store_true", help="창 목록만 출력하고 종료")
    parser.add_argument("--watch", action="store_true", help="캡처 후 선택한 창의 위치와 크기 변화를 추적")
    parser.add_argument("--output", type=Path, help="캡처 이미지 저장 위치 (기본: captures 폴더)")
    parser.add_argument("--capture-only", action="store_true", help="OCR 없이 캡처만 저장")
    args = parser.parse_args()

    windows = find_windows()
    if not windows:
        print("표시 중인 창을 찾지 못했습니다.")
        return 1

    if args.list:
        print("창 목록 (Arknights.exe 우선; Games.exe 런처 제외):")
        for index, item in enumerate(windows, 1):
            width, height = item.size
            print(f"{index:>2}. [{item.category}] {item.title} — {item.process} — {width}×{height} — HWND {item.handle}")
        return 0

    game_windows = [item for item in windows if item.process.casefold() == "arknights.exe"]
    if len(game_windows) == 1:
        window = game_windows[0]
        print(f"명일방주 창을 자동으로 찾았습니다: {window.title} — {window.process} — HWND {window.handle}")
    else:
        print("명일방주 창을 하나로 자동 식별하지 못했습니다. 창을 직접 선택해 주세요.")
        for index, item in enumerate(windows, 1):
            width, height = item.size
            print(f"{index:>2}. [{item.category}] {item.title} — {item.process} — {width}×{height} — HWND {item.handle}")
        try:
            choice = input("\n캡처할 창 번호 (종료: Enter): ").strip()
            if not choice:
                return 0
            window = windows[int(choice) - 1]
        except (ValueError, IndexError):
            print("목록에 있는 번호를 입력해 주세요.")
            return 2
        print(f"선택한 창: {window.title} (HWND {window.handle})")

    output = args.output or Path(__file__).resolve().parent / "captures" / f"arknights_{datetime.now():%Y%m%d_%H%M%S}.bmp"
    try:
        width, height = capture_window(window, output)
    except (OSError, RuntimeError) as error:
        print(f"화면 캡처 실패: {error}")
        return 1
    print(f"화면 캡처 완료: {width}×{height} → {output.resolve()}")
    if not args.capture_only:
        try:
            detections = recognize_text(output)
        except (ImportError, RuntimeError, OSError, ValueError) as error:
            print(f"OCR 실행 실패: {error}")
            if "설치되어 있지 않습니다" in str(error):
                print("설치 안내: requirements-ocr.txt와 PaddlePaddle 설치 안내를 확인하세요.")
            return 1

        result_path = output.with_suffix(".json")
        result_path.write_text(
            json.dumps({"image": str(output.resolve()), "detections": detections}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"OCR 결과: {len(detections)}개 텍스트 → {result_path.resolve()}")
        for item in detections:
            score = "?" if item["score"] is None else f"{item['score']:.2f}"
            print(f"[{score}] {item['text']} | 위치: {item['box']}")
        if not detections:
            print("텍스트가 검출되지 않았습니다.")
    if args.watch:
        _watch(window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
