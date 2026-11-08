"""Tkinter control panel and overlay child entry point."""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser

tk = None
messagebox = None
ttk = None
rounded_avatar = None


def _load_tkinter() -> None:
    global tk, messagebox, ttk
    import tkinter as tkinter_module
    from tkinter import messagebox as messagebox_module
    from tkinter import ttk as ttk_module
    tk = tkinter_module
    messagebox = messagebox_module
    ttk = ttk_module

from app_paths import APP_VERSION, glossary_path, icon_path, install_dir, overlay_log_path
from deepl_translate import GLOSSARY_NAME_PREFIX, create_glossary, delete_glossary, list_glossaries
from dictionary_updater import (
    PUBLIC_DICTIONARY_REPO, dictionary_update_available, latest_release, update_dictionary, version_newer,
)

RELEASES_URL = f"https://github.com/{PUBLIC_DICTIONARY_REPO}/releases/latest"
from secure_settings import load_settings, save_settings
from window_finder import Window, find_windows


def _hide_subprocess_consoles() -> None:
    """Stop console tools run by libraries from flashing a window.

    A windowed executable has no console, so each console child would open its own.
    """
    original_init = subprocess.Popen.__init__

    def init_without_console(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = init_without_console
    # os.system goes through the C runtime, which subprocess flags cannot reach.
    os.system = lambda command: subprocess.call(command, shell=True)


def _overlay_child(hwnd: int) -> int:
    _hide_subprocess_consoles()
    # The parent redirects stdio to overlay.log. Python would otherwise block-buffer it in
    # the ANSI code page, which loses messages when the child is terminated.
    if sys.stdout is None or sys.stderr is None:
        log_handle = overlay_log_path().open("a", encoding="utf-8", buffering=1)
        sys.stdout = log_handle
        sys.stderr = log_handle
    else:
        for stream in (sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    windows = find_windows()
    target = next((window for window in windows if window.handle == hwnd), None)
    if target is None:
        print(f"선택한 게임 창을 찾지 못했습니다. HWND={hwnd}")
        return 2
    from screen_translator import ScreenTranslator

    try:
        app = ScreenTranslator(target)
        app.mainloop()
    except (OSError, RuntimeError) as error:
        print(f"오버레이 오류: {error}")
        return 1
    return 0


def _set_window_icon(root) -> None:
    try:
        # Without its own ID, a script run from source shows Python's icon on the taskbar.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ATKR.ScreenTranslator")
        root.iconbitmap(default=str(icon_path()))
    except (OSError, tk.TclError):
        pass


def read_glossary(path: Path) -> list[tuple[str, str]]:
    """(source, target) pairs from DeepL's CSV format: source, target, source language, target language."""
    entries: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            # TSV upload cannot carry tabs or line breaks inside a term.
            source, target = (" ".join(cell.split()) for cell in row[:2])
            if source and target:
                entries.setdefault(source, target)
    return list(entries.items())


def upload_glossary(auth_key: str) -> str:
    """Put the bundled glossary on the key's DeepL account, replacing older app glossaries."""
    path = glossary_path()
    name = path.stem.removeprefix("glossary_")
    entries = read_glossary(path)
    if not entries:
        raise RuntimeError(f"용어집 파일이 비어 있습니다: {path}")
    try:
        existing = list_glossaries(auth_key, GLOSSARY_NAME_PREFIX, "ZH", "KO")
    except OSError as error:
        raise RuntimeError(f"계정의 용어집을 확인하지 못했습니다: {error}") from error
    if any(item.get("name") == name and item.get("ready") and item.get("entry_count") == len(entries) for item in existing):
        return f"이미 등록되어 있습니다: {name} (용어 {len(entries):,}개)"
    created = create_glossary(auth_key, name, "ZH", "KO", entries)
    # Only after the new glossary exists, so a failed upload leaves the old one in use.
    failed = 0
    for item in existing:
        try:
            delete_glossary(auth_key, str(item["glossary_id"]))
        except RuntimeError:
            failed += 1
    count = int(created.get("entry_count") or len(entries))
    message = f"용어집 등록 완료: {name} (용어 {count:,}개)."
    if failed:
        message += f" 이전 용어집 {failed}개는 지우지 못했지만, 새 용어집이 사용됩니다."
    return message


class TranslatorGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("명일방주 화면 번역기")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.settings = load_settings()
        self.windows: list[Window] = []
        self.window_by_label: dict[str, Window] = {}
        self.child: subprocess.Popen | None = None
        self.busy_update = False
        self.busy_glossary = False

        self.api_key = tk.StringVar(value=self.settings.get("api_key", ""))
        self.show_api_key = tk.BooleanVar(value=False)
        self.save_api_key = tk.BooleanVar(value=bool(self.settings.get("api_key")))
        self.window_choice = tk.StringVar()
        self.status = tk.StringVar(value="게임 창을 찾고 있습니다.")
        self.dictionary_status = tk.StringVar(value="현재 버전 확인 중")
        self.app_status = tk.StringVar(value=f"현재 {APP_VERSION} · 새 버전 확인 중")
        self.release_url = RELEASES_URL

        self._build_ui()
        # Fit the height to the content, which depends on the display scale.
        self.root.update_idletasks()
        height = self.root.winfo_reqheight() + 12
        self.root.geometry(f"620x{height}")
        self.root.minsize(540, height)
        self.refresh_windows()
        self._refresh_dictionary_status()
        self.root.after(500, self._poll_child)
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self) -> None:
        """Ask GitHub once at start whether a newer app release or dictionary exists. Nothing is downloaded."""
        try:
            release = latest_release()
            app_result = (release, None)
        except Exception as error:
            app_result = (None, str(error))
        try:
            dictionary_result = (dictionary_update_available(), None)
        except Exception as error:
            dictionary_result = (None, str(error))
        self.root.after(0, lambda: self._updates_checked(app_result, dictionary_result))

    def _updates_checked(self, app_result, dictionary_result) -> None:
        release, app_error = app_result
        notices = []
        if release is None:
            self.app_status.set(f"현재 {APP_VERSION} · 새 버전 확인 실패")
        elif version_newer(release["version"], APP_VERSION):
            self.release_url = release["url"]
            self.app_status.set(f"현재 {APP_VERSION} · 새 버전 {release['tag']}이 있습니다")
            self.release_button.configure(text="새 버전 받기", style="Primary.TButton")
            notices.append(f"새 앱 버전 {release['tag']}이 있습니다.")
        else:
            self.app_status.set(f"현재 {APP_VERSION} · 최신 버전")
        found, _dictionary_error = dictionary_result
        # The button already shows progress while an update the user started is running.
        if found is not None and found[1] and not self.busy_update:
            self.dictionary_status.set(f"{self.dictionary_status.get()} · 새 사전 {found[0]} 있음")
            self.update_button.configure(text="사전 업데이트", style="Primary.TButton")
            notices.append("새 사전이 있습니다.")
        if notices and (self.child is None or self.child.poll() is not None):
            self.status.set(" ".join(notices))

    def open_release(self) -> None:
        webbrowser.open(self.release_url)

    def _build_ui(self) -> None:
        card = ttk.Frame(self.root, padding=(26, 8, 26, 22))
        card.pack(fill="both", expand=True)

        header = ttk.Frame(card)
        header.pack(fill="x", pady=(0, 16))
        self.avatar = rounded_avatar(icon_path(), 46)
        if self.avatar:
            ttk.Label(header, image=self.avatar).pack(side="left", padx=(0, 12))
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="명일방주 화면 번역기", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="중국 서버 게임 화면을 캡처해 한섭 사전과 DeepL로 번역합니다.", style="Hint.TLabel").pack(anchor="w")

        ttk.Label(card, text="게임 창", style="Field.TLabel").pack(anchor="w")
        window_row = ttk.Frame(card)
        window_row.pack(fill="x", pady=(4, 14))
        self.window_combo = ttk.Combobox(window_row, textvariable=self.window_choice, state="readonly")
        self.window_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(window_row, text="새로고침", command=self.refresh_windows).pack(side="left")

        ttk.Label(card, text="DeepL API 키", style="Field.TLabel").pack(anchor="w")
        key_row = ttk.Frame(card)
        key_row.pack(fill="x", pady=(4, 8))
        self.key_entry = ttk.Entry(key_row, textvariable=self.api_key, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Checkbutton(key_row, text="표시", style="Toggle.TCheckbutton", variable=self.show_api_key, command=self._toggle_key_visibility).pack(side="left")
        ttk.Checkbutton(card, text="API 키를 이 Windows 계정에 암호화해 저장", style="Toggle.TCheckbutton", variable=self.save_api_key).pack(anchor="w", pady=(0, 14))

        app_row = ttk.Frame(card)
        app_row.pack(fill="x", pady=(0, 14))
        app_text = ttk.Frame(app_row)
        app_text.pack(side="left", fill="x", expand=True)
        ttk.Label(app_text, text="앱 버전", style="Field.TLabel").pack(anchor="w")
        ttk.Label(app_text, textvariable=self.app_status, style="Hint.TLabel").pack(anchor="w")
        self.release_button = ttk.Button(app_row, text="릴리스 열기", command=self.open_release)
        self.release_button.pack(side="right")

        dictionary_row = ttk.Frame(card)
        dictionary_row.pack(fill="x", pady=(0, 14))
        dictionary_text = ttk.Frame(dictionary_row)
        dictionary_text.pack(side="left", fill="x", expand=True)
        ttk.Label(dictionary_text, text="GitHub 사전", style="Field.TLabel").pack(anchor="w")
        ttk.Label(dictionary_text, textvariable=self.dictionary_status, style="Hint.TLabel").pack(anchor="w")
        self.update_button = ttk.Button(dictionary_row, text="업데이트 확인", command=self.update_dictionary)
        self.update_button.pack(side="right")

        glossary_row = ttk.Frame(card)
        glossary_row.pack(fill="x", pady=(0, 14))
        glossary_text = ttk.Frame(glossary_row)
        glossary_text.pack(side="left", fill="x", expand=True)
        ttk.Label(glossary_text, text="DeepL 용어집", style="Field.TLabel").pack(anchor="w")
        ttk.Label(glossary_text, text="사전에 없는 문구의 용어를 공식 표기로 맞춥니다 (계정당 한 번 등록)", style="Hint.TLabel", wraplength=400).pack(anchor="w")
        self.glossary_button = ttk.Button(glossary_row, text="용어집 등록", command=self.upload_glossary)
        self.glossary_button.pack(side="right")

        ttk.Frame(card, style="Line.TFrame", height=1).pack(fill="x", pady=(0, 14))
        actions = ttk.Frame(card)
        actions.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(actions, text="번역 시작", style="Primary.TButton", command=self.start_overlay)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="번역 중지", command=self.stop_overlay, state="disabled")
        self.stop_button.pack(side="left", padx=8)

        ttk.Label(card, textvariable=self.status, wraplength=540).pack(anchor="w", pady=(0, 4))
        ttk.Label(card, text="단축키: Ctrl+S 표시/숨김  |  Ctrl+Shift+S 종료", style="Hint.TLabel").pack(anchor="w")

    def _toggle_key_visibility(self) -> None:
        self.key_entry.configure(show="" if self.show_api_key.get() else "•")

    def _save_settings(self) -> None:
        settings = {}
        if self.save_api_key.get() and self.api_key.get().strip():
            settings["api_key"] = self.api_key.get().strip()
        try:
            save_settings(settings)
        except OSError as error:
            self.status.set(f"설정을 암호화해 저장하지 못했습니다: {error}")

    def refresh_windows(self) -> None:
        self.windows = find_windows()
        self.window_by_label.clear()
        labels = []
        for window in self.windows:
            width, height = window.size
            label = f"{window.title} - {window.process} - {width}x{height} - HWND {window.handle}"
            labels.append(label)
            self.window_by_label[label] = window
        self.window_combo.configure(values=labels)
        games = [window for window in self.windows if window.process.casefold() == "arknights.exe"]
        chosen = games[0] if len(games) == 1 else None
        if chosen:
            self.window_choice.set(next(label for label, window in self.window_by_label.items() if window.handle == chosen.handle))
            self.status.set(f"명일방주 게임 창을 찾았습니다: HWND {chosen.handle}")
        elif labels:
            if self.window_choice.get() not in self.window_by_label:
                self.window_choice.set(labels[0])
            self.status.set("번역할 게임 창을 목록에서 선택하세요.")
        else:
            self.window_choice.set("")
            self.status.set("표시 중인 게임 창이 없습니다. 명일방주를 실행한 뒤 새로고침하세요.")

    def _selected_window(self) -> Window | None:
        return self.window_by_label.get(self.window_choice.get())

    def _command(self, hwnd: int) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--overlay", "--hwnd", str(hwnd)]
        return [sys.executable, str(Path(__file__).resolve()), "--overlay", "--hwnd", str(hwnd)]

    def start_overlay(self) -> None:
        if self.child and self.child.poll() is None:
            return
        window = self._selected_window()
        if window is None:
            messagebox.showerror("게임 창 없음", "명일방주 게임 창을 새로고침하고 선택하세요.", parent=self.root)
            return
        key = self.api_key.get().strip()
        if self.save_api_key.get() and not key:
            self.save_api_key.set(False)
        self._save_settings()
        environment = os.environ.copy()
        environment.pop("DEEPL_AUTH_KEY", None)
        if key:
            environment["DEEPL_AUTH_KEY"] = key
        log_file = overlay_log_path().open("a", encoding="utf-8", buffering=1)
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.child = subprocess.Popen(
                self._command(window.handle), cwd=install_dir(), env=environment,
                stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except OSError as error:
            log_file.close()
            messagebox.showerror("실행 실패", str(error), parent=self.root)
            return
        log_file.close()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("번역 오버레이 실행 중입니다. Ctrl+S 표시/숨김, Ctrl+Shift+S 종료")

    def stop_overlay(self) -> None:
        child = self.child
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
        self.child = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("번역 오버레이가 중지되었습니다.")

    def _poll_child(self) -> None:
        if self.child and self.child.poll() is not None:
            code = self.child.returncode
            self.child = None
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status.set("오버레이가 종료되었습니다." if code == 0 else f"오버레이가 종료되었습니다. 로그: {overlay_log_path()}")
        self.root.after(500, self._poll_child)

    def _refresh_dictionary_status(self) -> None:
        from app_paths import active_runtime_dictionary_path, runtime_dictionary_pointer
        import sqlite3
        path = active_runtime_dictionary_path()
        if not path.is_file():
            self.dictionary_status.set("사전 없음")
            return
        try:
            connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
            connection.execute("SELECT 1 FROM lookup LIMIT 1").fetchone()
            connection.close()
            pointer = runtime_dictionary_pointer()
            # No pointer simply means no update was installed yet.
            versioned_name = pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else ""
            if versioned_name.startswith("runtime_dictionary_") and versioned_name.endswith(".sqlite3"):
                version = versioned_name[len("runtime_dictionary_"):-len(".sqlite3")]
            else:
                version = "기본 포함본"
            self.dictionary_status.set(f"현재 버전: {version}")
        except (OSError, UnicodeDecodeError, sqlite3.Error):
            self.dictionary_status.set("현재 사전 확인 실패")

    def update_dictionary(self) -> None:
        if self.busy_update:
            return
        self.busy_update = True
        self.update_button.configure(state="disabled", text="확인 중...")
        self.status.set("GitHub 저장소의 사전을 확인하고, 새 버전이 있으면 내려받습니다.")
        threading.Thread(
            target=self._update_worker,
            daemon=True,
        ).start()

    def _update_worker(self) -> None:
        try:
            result = update_dictionary()
            self.root.after(0, lambda: self._update_done(result, None))
        except Exception as error:
            self.root.after(0, lambda message=str(error): self._update_done(None, message))

    def _update_done(self, result, error: str | None) -> None:
        self.busy_update = False
        self.update_button.configure(state="normal", text="업데이트 확인", style="TButton")
        if error:
            self.status.set(f"사전 업데이트 실패: {error}")
        else:
            tag, changed, _lookup, _terms = result
            state = "업데이트 완료" if changed else "최신 버전"
            self.status.set(f"{state}: {tag}")
            self.dictionary_status.set(f"현재 버전: {tag}")

    def upload_glossary(self) -> None:
        if self.busy_glossary:
            return
        auth_key = self.api_key.get().strip()
        if not auth_key:
            messagebox.showerror("API 키 없음", "DeepL API 키를 먼저 입력하세요.", parent=self.root)
            return
        if not messagebox.askokcancel(
            "DeepL 용어집 등록",
            "이 API 키의 DeepL 계정에 명일방주 용어집(중국어→한국어)을 등록합니다.\n"
            f"이름이 {GLOSSARY_NAME_PREFIX}로 시작하는 이전 용어집은 새 용어집으로 바꿉니다.\n"
            "용어집 등록은 번역 사용량에 포함되지 않습니다.",
            parent=self.root,
        ):
            return
        self.busy_glossary = True
        self.glossary_button.configure(state="disabled", text="등록 중...")
        self.status.set("DeepL 계정에 용어집을 등록하고 있습니다.")
        threading.Thread(target=self._glossary_worker, args=(auth_key,), daemon=True).start()

    def _glossary_worker(self, auth_key: str) -> None:
        try:
            message = upload_glossary(auth_key)
            self.root.after(0, lambda: self._glossary_done(message, None))
        except Exception as error:
            self.root.after(0, lambda text=str(error): self._glossary_done(None, text))

    def _glossary_done(self, message: str | None, error: str | None) -> None:
        self.busy_glossary = False
        self.glossary_button.configure(state="normal", text="용어집 등록")
        if error:
            self.status.set(f"용어집 등록 실패: {error}")
            return
        if self.child is not None and self.child.poll() is None:
            message += " 번역 중지 후 다시 시작하면 적용됩니다."
        self.status.set(message)

    def close(self) -> None:
        self.stop_overlay()
        self._save_settings()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--overlay", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hwnd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.overlay:
        if not args.hwnd:
            return 2
        return _overlay_child(args.hwnd)
    _load_tkinter()
    from ui_theme import apply_theme, blend_title_bar
    global rounded_avatar
    from ui_theme import rounded_avatar
    root = tk.Tk()
    _set_window_icon(root)
    apply_theme(root)
    TranslatorGui(root)
    blend_title_bar(root, icon_path())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
