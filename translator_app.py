"""Tkinter control panel and overlay child entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import threading

tk = None
messagebox = None
ttk = None


def _load_tkinter() -> None:
    global tk, messagebox, ttk
    import tkinter as tkinter_module
    from tkinter import messagebox as messagebox_module
    from tkinter import ttk as ttk_module
    tk = tkinter_module
    messagebox = messagebox_module
    ttk = ttk_module

from app_paths import install_dir, overlay_log_path
from dictionary_updater import update_dictionary
from secure_settings import load_settings, save_settings
from window_finder import Window, find_windows


def _hide_subprocess_consoles() -> None:
    """Stop console tools run by libraries (Paddle's `where ccache`) from flashing a window.

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


class TranslatorGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("명일방주 화면 번역기")
        self.root.geometry("600x390")
        self.root.minsize(540, 360)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.settings = load_settings()
        self.windows: list[Window] = []
        self.window_by_label: dict[str, Window] = {}
        self.child: subprocess.Popen | None = None
        self.busy_update = False

        self.api_key = tk.StringVar(value=self.settings.get("api_key", ""))
        self.show_api_key = tk.BooleanVar(value=False)
        self.save_api_key = tk.BooleanVar(value=bool(self.settings.get("api_key")))
        self.window_choice = tk.StringVar()
        self.status = tk.StringVar(value="게임 창을 찾고 있습니다.")
        self.dictionary_status = tk.StringVar(value="현재 버전 확인 중")

        self._build_ui()
        self.refresh_windows()
        self._refresh_dictionary_status()
        self.root.after(500, self._poll_child)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="명일방주 화면 번역기", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(container, text="중국 서버 게임 화면을 캡처해 한섭 사전과 DeepL로 번역합니다.").pack(anchor="w", pady=(3, 14))

        window_row = ttk.Frame(container)
        window_row.pack(fill="x", pady=4)
        ttk.Label(window_row, text="게임 창", width=12).pack(side="left")
        self.window_combo = ttk.Combobox(window_row, textvariable=self.window_choice, state="readonly")
        self.window_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(window_row, text="새로고침", command=self.refresh_windows).pack(side="left")

        key_row = ttk.Frame(container)
        key_row.pack(fill="x", pady=4)
        ttk.Label(key_row, text="DeepL API 키", width=12).pack(side="left")
        self.key_entry = ttk.Entry(key_row, textvariable=self.api_key, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Checkbutton(key_row, text="표시", variable=self.show_api_key, command=self._toggle_key_visibility).pack(side="left")

        options_row = ttk.Frame(container)
        options_row.pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(options_row, text="API 키를 이 Windows 계정에 암호화해 저장", variable=self.save_api_key).pack(side="left", padx=(94, 0))

        dictionary_row = ttk.Frame(container)
        dictionary_row.pack(fill="x", pady=4)
        ttk.Label(dictionary_row, text="GitHub 사전", width=12).pack(side="left")
        ttk.Label(dictionary_row, textvariable=self.dictionary_status).pack(side="left", fill="x", expand=True)
        self.update_button = ttk.Button(dictionary_row, text="업데이트 확인", command=self.update_dictionary)
        self.update_button.pack(side="left")

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(8, 12))
        self.start_button = ttk.Button(actions, text="번역 시작", command=self.start_overlay)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="번역 중지", command=self.stop_overlay, state="disabled")
        self.stop_button.pack(side="left", padx=8)

        ttk.Separator(container).pack(fill="x", pady=8)
        ttk.Label(container, textvariable=self.status, wraplength=530).pack(anchor="w", pady=5)
        ttk.Label(container, text="오버레이 단축키: Ctrl+S 표시/숨김  |  Ctrl+Shift+S 종료", foreground="#555").pack(anchor="w", pady=(5, 0))

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
        self.update_button.configure(state="normal", text="업데이트 확인")
        if error:
            self.status.set(f"사전 업데이트 실패: {error}")
        else:
            tag, changed, _lookup, _terms = result
            state = "업데이트 완료" if changed else "최신 버전"
            self.status.set(f"{state}: {tag}")
            self.dictionary_status.set(f"현재 버전: {tag}")

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
    root = tk.Tk()
    TranslatorGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
