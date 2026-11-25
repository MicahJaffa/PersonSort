from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog
import webbrowser
from typing import Callable


BG_DARK = "#0f172a"
BG_CARD = "#1e293b"
BORDER_COLOR = "#334155"
TEXT_PRIMARY = "#f8fafc"
TEXT_MUTED = "#94a3b8"
ACCENT_BLUE = "#3b82f6"
ACCENT_BLUE_HOVER = "#2563eb"
BTN_SECONDARY = "#334155"
BTN_SECONDARY_HOVER = "#475569"
SUCCESS_GREEN = "#10b981"
DANGER_RED = "#ef4444"
DANGER_RED_HOVER = "#dc2626"


class PersonSortControlWindow:
    """Small native desktop window that controls the PersonSort web server lifecycle."""

    def __init__(
        self,
        url: str,
        version: str,
        on_quit: Callable[[], None],
        open_browser_on_start: bool = True,
    ) -> None:
        self.url = url
        self.version = version
        self.on_quit = on_quit
        self.open_browser_on_start = open_browser_on_start

        self._lock = threading.Lock()
        self._closed = False
        self._quit_called = False
        self._copy_reset_after_id: str | None = None

        self.root = tk.Tk()
        self.root.title(f"PersonSort v{self.version}")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_quit)

        self._status_hint_var = tk.StringVar(
            value="Keep this window open while using PersonSort in your browser."
        )
        self._build_ui()
        self._center_window(460, 290)

    def _center_window(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=BG_DARK, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        card = tk.Frame(
            outer,
            bg=BG_CARD,
            padx=20,
            pady=18,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
        )
        card.pack(fill="both", expand=True)

        header_row = tk.Frame(card, bg=BG_CARD)
        header_row.pack(fill="x")

        title_lbl = tk.Label(
            header_row,
            text=f"PersonSort v{self.version}",
            bg=BG_CARD,
            fg=TEXT_PRIMARY,
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        )
        title_lbl.pack(side="left")

        badge_frame = tk.Frame(header_row, bg=BG_CARD)
        badge_frame.pack(side="right")

        dot = tk.Canvas(
            badge_frame,
            width=10,
            height=10,
            bg=BG_CARD,
            highlightthickness=0,
        )
        dot.create_oval(1, 1, 9, 9, fill=SUCCESS_GREEN, outline=SUCCESS_GREEN)
        dot.pack(side="left", padx=(0, 6), pady=2)

        status_lbl = tk.Label(
            badge_frame,
            text="Running",
            bg=BG_CARD,
            fg=SUCCESS_GREEN,
            font=("Segoe UI", 9, "bold"),
        )
        status_lbl.pack(side="left")

        url_box = tk.Frame(
            card,
            bg=BG_DARK,
            padx=12,
            pady=9,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
        )
        url_box.pack(fill="x", pady=(14, 10))

        url_caption = tk.Label(
            url_box,
            text="Web Interface URL",
            bg=BG_DARK,
            fg=TEXT_MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        )
        url_caption.pack(fill="x")

        url_link = tk.Label(
            url_box,
            text=self.url,
            bg=BG_DARK,
            fg=TEXT_PRIMARY,
            font=("Consolas", 10),
            anchor="w",
            cursor="hand2",
        )
        url_link.pack(fill="x", pady=(2, 0))
        url_link.bind("<Button-1>", lambda _event: self._handle_open_browser())

        hint_lbl = tk.Label(
            card,
            textvariable=self._status_hint_var,
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
            wraplength=390,
            justify="left",
            anchor="w",
        )
        hint_lbl.pack(fill="x", pady=(2, 16))

        btn_row = tk.Frame(card, bg=BG_CARD)
        btn_row.pack(fill="x")

        open_btn = tk.Button(
            btn_row,
            text="Open PersonSort in Browser",
            command=self._handle_open_browser,
            bg=ACCENT_BLUE,
            fg=TEXT_PRIMARY,
            activebackground=ACCENT_BLUE_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=7,
            cursor="hand2",
            bd=0,
        )
        open_btn.pack(side="left", fill="x", expand=True)

        copy_btn = tk.Button(
            btn_row,
            text="Copy Link",
            command=self._handle_copy_link,
            bg=BTN_SECONDARY,
            fg=TEXT_PRIMARY,
            activebackground=BTN_SECONDARY_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            font=("Segoe UI", 9),
            padx=12,
            pady=7,
            cursor="hand2",
            bd=0,
        )
        copy_btn.pack(side="left", padx=(8, 0))

        quit_btn = tk.Button(
            card,
            text="Quit PersonSort",
            command=self._handle_quit,
            bg=DANGER_RED,
            fg=TEXT_PRIMARY,
            activebackground=DANGER_RED_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=7,
            cursor="hand2",
            bd=0,
        )
        quit_btn.pack(fill="x", pady=(10, 0))

        footer_lbl = tk.Label(
            card,
            text="Closing this window will stop PersonSort cleanly.",
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        )
        footer_lbl.pack(fill="x", pady=(10, 0))

    def _handle_open_browser(self) -> None:
        try:
            webbrowser.open(self.url)
        except Exception:
            self._status_hint_var.set(f"Open this link manually in your browser: {self.url}")

    def _handle_copy_link(self) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.url)
            self.root.update()
            self._status_hint_var.set(f"Copied {self.url} to clipboard.")
            if self._copy_reset_after_id is not None:
                try:
                    self.root.after_cancel(self._copy_reset_after_id)
                except Exception:
                    pass
            self._copy_reset_after_id = self.root.after(
                3000,
                lambda: self._status_hint_var.set(
                    "Keep this window open while using PersonSort in your browser."
                ),
            )
        except Exception:
            self._status_hint_var.set(f"Link: {self.url}")

    def _handle_quit(self) -> None:
        should_call = False
        with self._lock:
            if not self._quit_called:
                self._quit_called = True
                should_call = True
        if should_call:
            try:
                self.on_quit()
            except Exception:
                pass
        self._destroy_only()

    def _destroy_only(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def request_close(self) -> None:
        """Thread-safe request to close the control window (e.g., from HTTP shutdown)."""
        with self._lock:
            if self._closed:
                return
        try:
            self.root.after(0, self._destroy_only)
        except Exception:
            pass

    def ask_directory(self, initial_dir: str = "") -> str:
        """Open a native folder picker safely on the main Tkinter thread."""
        with self._lock:
            if self._closed:
                return ""

        done = threading.Event()
        result: dict[str, str] = {"path": ""}

        def _open_dialog() -> None:
            try:
                with self._lock:
                    if self._closed:
                        return
                kwargs: dict[str, object] = {
                    "parent": self.root,
                    "title": "Choose Output Folder",
                }
                if initial_dir:
                    kwargs["initialdir"] = initial_dir
                selected = filedialog.askdirectory(**kwargs)
                result["path"] = str(selected or "")
            except Exception:
                result["path"] = ""
            finally:
                done.set()

        try:
            self.root.after(0, _open_dialog)
        except Exception:
            return ""

        while not done.wait(timeout=0.25):
            with self._lock:
                if self._closed:
                    return ""
        return result["path"]

    def run(self) -> None:
        """Start optional browser launch and enter the Tkinter main loop."""
        if self.open_browser_on_start:
            self.root.after(350, self._handle_open_browser)
        try:
            self.root.mainloop()
        finally:
            with self._lock:
                self._closed = True
