"""Dialog «Area schermo»: selezione di dove registrare (tutto/finestra/regione)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import config
from ..storage import screenrec
from .theme import BG, CARD, FONT_SMALL, INK, MUTED


class RegionSelector(tk.Toplevel):
    """Overlay a tutto schermo: trascina per scegliere la regione."""

    def __init__(self, parent: tk.Misc, on_done, on_cancel=None):
        super().__init__(parent)
        self.on_done = on_done
        self.on_cancel = on_cancel
        self._start = None
        self._rect_id = None

        try:
            import ctypes
            from ctypes import wintypes
            p = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            x, y = p.x, p.y
        except Exception:                       # noqa: BLE001
            x = y = 0
        mon = screenrec.monitor_at(x, y)

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}"
                      f"+{mon['top']}")
        self.configure(bg="#000000")
        self.attributes("-alpha", 0.35)
        self._mon = mon

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            mon["width"] // 2, 40, fill="white",
            font=("Segoe UI Semibold", 13),
            text="Trascina per selezionare la regione · ESC annulla")

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda e: self._cancel())
        self.focus_force()
        self.grab_set()

    def _press(self, ev) -> None:
        self._start = (ev.x, ev.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
            self._rect_id = None

    def _drag(self, ev) -> None:
        if not self._start:
            return
        x0, y0 = self._start
        if self._rect_id:
            self.canvas.coords(self._rect_id, x0, y0, ev.x, ev.y)
        else:
            self._rect_id = self.canvas.create_rectangle(
                x0, y0, ev.x, ev.y, outline="#3b82f6", width=3)

    def _release(self, ev) -> None:
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = ev.x, ev.y
        self._start = None
        if abs(x1 - x0) < 10 or abs(y1 - y0) < 10:
            self._cancel()
            return
        left = self._mon["left"] + min(x0, x1)
        top = self._mon["top"] + min(y0, y1)
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        cb, self.on_done = self.on_done, None
        self.destroy()
        if cb:
            cb((left, top, w, h))

    def _cancel(self) -> None:
        cb, self.on_cancel = self.on_cancel, None
        self.destroy()
        if cb:
            cb()


class ScreenDialog(tk.Toplevel):
    """Sceglie l'area della registrazione video: nessuna registrazione qui.

    La registrazione parte solo dal pulsante AVVIA della finestra principale
    (così audio e video nascono insieme, in un'unica sessione).
    """

    def __init__(self, parent: tk.Misc, on_area=None):
        super().__init__(parent)
        self.title("Area di registrazione video")
        self.resizable(False, False)
        self.configure(bg=CARD)
        self.on_area = on_area
        self._selector: RegionSelector | None = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        main = tk.Frame(self, bg=CARD, padx=16, pady=14)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)

        tk.Label(main, text="REGISTRAZIONE VIDEO", bg=CARD, fg="#38bdf8",
                 font=("Segoe UI Semibold", 11)).grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        tk.Label(main,
                 text="Scegli cosa catturare quando parti con AVVIA:\n"
                      "l'audio della sessione finisce dentro il MP4.",
                 bg=CARD, fg=MUTED, font=FONT_SMALL, justify="left",
                 wraplength=380).grid(row=1, column=0, sticky="w",
                                      pady=(0, 8))

        self.mode = tk.StringVar(value="screen")
        modes = [("Tutto schermo (monitor virtuale)", "screen"),
                 ("Finestra specifica", "window"),
                 ("Selezione con il mouse", "region")]
        r = 2
        for text, val in modes:
            ttk.Radiobutton(main, text=text, value=val, variable=self.mode,
                            command=self._sync_mode).grid(
                row=r, column=0, sticky="w", pady=1)
            r += 1

        self.frm_win = tk.Frame(main, bg=CARD)
        self.frm_win.grid(row=r, column=0, sticky="ew", pady=(4, 0))
        self.win_var = tk.StringVar()
        self.win_combo = ttk.Combobox(self.frm_win, textvariable=self.win_var,
                                      state="readonly", width=52)
        self.win_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(self.frm_win, text="Aggiorna",
                   command=self._load_windows).grid(
            row=0, column=1, padx=(6, 0))
        r += 1

        fbox = tk.Frame(main, bg=CARD)
        fbox.grid(row=r, column=0, sticky="w", pady=(10, 0))
        tk.Label(fbox, text="FPS", bg=CARD, fg=INK,
                 font=FONT_SMALL).pack(side="left")
        self.fps = tk.StringVar(value="15")
        ttk.Combobox(fbox, textvariable=self.fps, state="readonly",
                     values=("10", "15", "20", "24"), width=5).pack(
            side="left", padx=(6, 0))
        r += 1

        btns = tk.Frame(main, bg=CARD)
        btns.grid(row=r, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(btns, text="Usa questa area", style="Success.TButton",
                   command=self._ok).pack(side="left")
        ttk.Button(btns, text="Annulla", command=self._on_close).pack(
            side="left", padx=(8, 0))

        area = config.SETTINGS.get("screen_area") or {}
        if str(area.get("mode") or "screen") in ("screen", "window", "region"):
            self.mode.set(str(area.get("mode") or "screen"))
        fps = area.get("fps")
        if fps:
            self.fps.set(str(fps))
        self._load_windows()
        rect = area.get("rect")
        if self.mode.get() == "window" and isinstance(rect, (list, tuple)):
            for title, wr in self._windows.items():
                if list(wr) == list(rect):
                    self.win_var.set(title)
                    break
        self._sync_mode()
        self._center()

    # ------------------------------------------------------------------ #
    def _center(self) -> None:
        self.update_idletasks()
        pw, ph = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - pw) // 2}+{(sh - ph) // 3}")

    def _load_windows(self) -> None:
        wins = screenrec.list_windows(
            exclude_titles=("IACallMinute", "Area di registrazione"))
        titles = [w["title"] for w in wins]
        self._windows = {w["title"]: w["rect"] for w in wins}
        self.win_combo.configure(values=titles)
        if titles and not self.win_var.get():
            self.win_var.set(titles[0])

    def _sync_mode(self) -> None:
        state = "normal" if self.mode.get() == "window" else "disabled"
        self.win_combo.configure(state=state)

    def _ok(self) -> None:
        mode = self.mode.get()
        try:
            if mode == "region":
                self._selector = RegionSelector(
                    self, on_done=self._region_done,
                    on_cancel=self._region_cancel)
                self.withdraw()
                return
            if mode == "window":
                title = self.win_var.get().strip()
                if not title or title not in self._windows:
                    messagebox.showinfo("Nessuna finestra",
                                        "Scegli una finestra dall'elenco.",
                                        parent=self)
                    return
                rect = self._windows[title]
            else:
                import mss
                with mss.mss() as sct:
                    mon = sct.monitors[0]
                rect = (mon["left"], mon["top"], mon["width"], mon["height"])
            self._save(mode, rect)
        except Exception as exc:                # noqa: BLE001
            messagebox.showerror("Area video", str(exc), parent=self)

    def _region_done(self, rect: tuple) -> None:
        self._selector = None
        self.deiconify()
        self._save("region", rect)

    def _region_cancel(self) -> None:
        self._selector = None
        self.deiconify()

    def _save(self, mode: str, rect: tuple) -> None:
        config.SETTINGS["screen_area"] = {
            "mode": mode, "rect": [int(v) for v in rect],
            "fps": int(self.fps.get())}
        config.save_settings()
        if self.on_area is not None:
            try:
                self.on_area(config.SETTINGS["screen_area"])
            except Exception:                   # noqa: BLE001
                pass
        self.destroy()

    def _on_close(self) -> None:
        if self._selector is not None and self._selector.winfo_exists():
            self._selector.destroy()
            self._selector = None
        self.destroy()
