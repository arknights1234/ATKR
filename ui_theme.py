"""Soft white look for the control panel. Imported by the GUI only, never by the overlay."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from io import BytesIO
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageChops, ImageDraw

WINDOW_BG = "#FFFFFF"
CARD_BG = "#FFFFFF"
BORDER = "#E4E4E4"
BORDER_FOCUS = "#A8A8A8"
TEXT = "#1E1E1E"
MUTED = "#7B7B7B"
PRIMARY = "#262626"
PRIMARY_HOVER = "#414141"
DISABLED_BG = "#E9E9E9"
DISABLED_TEXT = "#ABABAB"
HOVER_BG = "#F4F4F4"
PRESSED_BG = "#EBEBEB"
TOGGLE_OFF = "#D8D8D8"
FONT = "Malgun Gothic"
SUPERSAMPLE = 4
WTA_NONCLIENT = 1
WTNCA_NODRAWCAPTION = 0x1
WTNCA_NODRAWICON = 0x2
DWMWA_CAPTION_COLOR = 35
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
SM_CXICON = 11
SM_CYICON = 12


def _photo(image: Image.Image) -> tk.PhotoImage:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))


def _rounded(width: int, height: int, radius: int, fill: str, outline: str | None = None) -> tk.PhotoImage:
    """Antialiased rounded rectangle; ttk stretches its middle and keeps the corners."""
    scale = SUPERSAMPLE
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius * scale,
        fill=fill, outline=outline, width=scale if outline else 0,
    )
    return _photo(image.resize((width, height), Image.Resampling.LANCZOS))


def _toggle(on: bool, enabled: bool = True) -> tk.PhotoImage:
    width, height = 36, 20
    label_gap = 8  # Transparent margin: ttk layouts have no spacing option between elements.
    scale = SUPERSAMPLE
    track = (PRIMARY if on else TOGGLE_OFF) if enabled else DISABLED_BG
    image = Image.new("RGBA", ((width + label_gap) * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width * scale - 1, height * scale - 1), height * scale // 2, fill=track)
    inset = 3 * scale
    knob = height * scale - 2 * inset
    left = width * scale - inset - knob if on else inset
    draw.ellipse((left, inset, left + knob, inset + knob), fill="#FFFFFF")
    return _photo(image.resize((width + label_gap, height), Image.Resampling.LANCZOS))


def _chevron() -> tk.PhotoImage:
    width, height = 26, 18
    scale = SUPERSAMPLE
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    points = [(7 * scale, 7 * scale), (12 * scale, 12 * scale), (17 * scale, 7 * scale)]
    ImageDraw.Draw(image).line(points, fill=MUTED, width=int(1.6 * scale), joint="curve")
    return _photo(image.resize((width, height), Image.Resampling.LANCZOS))


def rounded_avatar(path, size: int) -> tk.PhotoImage | None:
    """Rounded square of the app icon for the header; the header simply omits it if unavailable."""
    try:
        with Image.open(path) as source:
            icon = source.convert("RGBA")
    except OSError:
        return None
    scale = SUPERSAMPLE
    big = icon.resize((size * scale, size * scale), Image.Resampling.LANCZOS)
    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big.width - 1, big.height - 1), size * scale // 4, fill=255)
    big.putalpha(ImageChops.multiply(big.getchannel("A"), mask))
    return _photo(big.resize((size, size), Image.Resampling.LANCZOS))


class _ThemeOptions(ctypes.Structure):
    _fields_ = [("flags", wintypes.DWORD), ("mask", wintypes.DWORD)]


def blend_title_bar(root: tk.Tk, icon) -> None:
    """Leave only the window buttons on the title bar, so it merges with the white window.

    The native frame stays, which keeps dragging, snapping and the taskbar entry working.
    The title stays too: it is only hidden from the bar, and the taskbar still shows it.
    """
    try:
        root.update_idletasks()
        user32 = ctypes.WinDLL("user32")
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        frame = user32.GetParent(root.winfo_id())
        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme.SetWindowThemeAttribute.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p, wintypes.UINT]
        options = _ThemeOptions(
            WTNCA_NODRAWCAPTION | WTNCA_NODRAWICON, WTNCA_NODRAWCAPTION | WTNCA_NODRAWICON,
        )
        uxtheme.SetWindowThemeAttribute(frame, WTA_NONCLIENT, ctypes.byref(options), ctypes.sizeof(options))
        # Tk's own icon overrides the flag above, so give the bar a transparent small one. The
        # taskbar and Alt+Tab fall back to that too unless the window has a real big icon.
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.CreateIcon.argtypes = [
            wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_ubyte, ctypes.c_ubyte,
            ctypes.c_char_p, ctypes.c_char_p,
        ]
        user32.CreateIcon.restype = wintypes.HICON
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        mask_size = 32 * 32 // 8
        blank = user32.CreateIcon(None, 32, 32, 1, 1, b"\xff" * mask_size, b"\x00" * mask_size)
        big = user32.LoadImageW(
            None, str(icon), IMAGE_ICON, user32.GetSystemMetrics(SM_CXICON), user32.GetSystemMetrics(SM_CYICON),
            LR_LOADFROMFILE,
        )
        # Without the real icon the blank one would reach the taskbar, so keep Tk's in that case.
        if big and blank:
            user32.SendMessageW(frame, WM_SETICON, ICON_BIG, big)
            user32.SendMessageW(frame, WM_SETICON, ICON_SMALL, blank)
        # Windows 11 can color the bar; older versions reject this harmlessly.
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        color = wintypes.DWORD(0x00FFFFFF)
        dwmapi.DwmSetWindowAttribute(frame, DWMWA_CAPTION_COLOR, ctypes.byref(color), ctypes.sizeof(color))
    except (OSError, AttributeError, tk.TclError):
        pass


def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=WINDOW_BG)
    # Tk drops images it has no Python reference to.
    images: dict[str, tk.PhotoImage] = {}
    root._theme_images = images  # type: ignore[attr-defined]

    def image(name: str, *args, **kwargs) -> tk.PhotoImage:
        images[name] = _rounded(*args, **kwargs)
        return images[name]

    style.configure(".", background=CARD_BG, foreground=TEXT, font=(FONT, 10), borderwidth=0)
    style.configure("TFrame", background=CARD_BG)
    style.configure("TLabel", background=CARD_BG, foreground=TEXT, font=(FONT, 10))
    style.configure("Title.TLabel", font=(FONT, 15, "bold"))
    style.configure("Field.TLabel", font=(FONT, 10, "bold"))
    style.configure("Hint.TLabel", foreground=MUTED, font=(FONT, 9))
    style.configure("Line.TFrame", background=BORDER)

    style.element_create(
        "Soft.button", "image", image("button", 40, 34, 11, CARD_BG, BORDER),
        ("disabled", image("button_disabled", 40, 34, 11, DISABLED_BG)),
        ("pressed", image("button_pressed", 40, 34, 11, PRESSED_BG, BORDER)),
        ("active", image("button_hover", 40, 34, 11, HOVER_BG, BORDER)),
        border=12, padding=0, sticky="nsew",
    )
    style.element_create(
        "Primary.button", "image", image("primary", 40, 34, 11, PRIMARY),
        ("disabled", image("primary_disabled", 40, 34, 11, DISABLED_BG)),
        ("pressed", image("primary_pressed", 40, 34, 11, "#000000")),
        ("active", image("primary_hover", 40, 34, 11, PRIMARY_HOVER)),
        border=12, padding=0, sticky="nsew",
    )
    for name, element in (("TButton", "Soft.button"), ("Primary.TButton", "Primary.button")):
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]}),
        ]})])
    style.configure("TButton", padding=(14, 6), anchor="center", foreground=TEXT, font=(FONT, 10))
    style.map("TButton", foreground=[("disabled", DISABLED_TEXT)], background=[("!disabled", CARD_BG)])
    style.configure("Primary.TButton", foreground="#FFFFFF", font=(FONT, 10, "bold"), padding=(18, 7))
    style.map("Primary.TButton", foreground=[("disabled", DISABLED_TEXT), ("!disabled", "#FFFFFF")])

    style.element_create(
        "Soft.field", "image", image("field", 40, 34, 11, CARD_BG, BORDER),
        ("focus", image("field_focus", 40, 34, 11, CARD_BG, BORDER_FOCUS)),
        border=12, padding=0, sticky="nsew",
    )
    style.layout("TEntry", [("Soft.field", {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]}),
    ]})])
    style.configure("TEntry", padding=(12, 7), foreground=TEXT, insertcolor=TEXT,
                    selectbackground="#DDDDDD", selectforeground=TEXT)

    images["chevron"] = _chevron()
    style.element_create("Soft.arrow", "image", images["chevron"], sticky="")
    style.layout("TCombobox", [("Soft.field", {"sticky": "nsew", "children": [
        ("Soft.arrow", {"side": "right", "sticky": ""}),
        ("Combobox.padding", {"expand": 1, "sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]}),
    ]})])
    style.configure("TCombobox", padding=(12, 7), foreground=TEXT, arrowsize=0)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", CARD_BG)], foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", CARD_BG)], selectforeground=[("readonly", TEXT)],
    )
    root.option_add("*TCombobox*Listbox.background", CARD_BG)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", HOVER_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.highlightThickness", 0)

    images["toggle_off"] = _toggle(False)
    images["toggle_on"] = _toggle(True)
    style.element_create(
        "Soft.toggle", "image", images["toggle_off"], ("selected", images["toggle_on"]), sticky="w",
    )
    style.layout("Toggle.TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        ("Soft.toggle", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "w"}),
    ]})])
    style.configure("Toggle.TCheckbutton", background=CARD_BG, foreground=TEXT, font=(FONT, 10), padding=0)
    style.map("Toggle.TCheckbutton", background=[("active", CARD_BG)])
