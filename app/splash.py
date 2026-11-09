"""Splash screen di avvio: muovibile e con barra di avanzamento.

Sostituisce la finestra statica di PyInstaller (nessun movimento, nessuno
stato). La finestra e' senza titlebar ma si trascina con il mouse su
qualsiasi punto, e la barra mostra l'avanzamento reale delle fasi.

Il caricamento pesante va eseguito in un thread e atteso con spin(): questo
metodo continua a pompare gli eventi Tk finche il thread vive, quindi la
finestra resta visibile, reattiva e trascinabile anche mentre l'app carica.
"""
from __future__ import annotations

import logging
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

log = logging.getLogger("iacm.splash")

W, H = 560, 320
BG = "#0f172a"
TRACK = "#334155"
ACCENT = "#38bdf8"
MUTED = "#94a3b8"

_ASSET = Path(__file__).resolve().parents[1] / "assets" / "splash.png"
BAR = dict(x=52, y=232, width=W - 104, height=10)


def dismiss_pyi() -> None:
    """Chiude lo splash di PyInstaller (presente solo in exe con --splash)."""
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:                          # noqa: BLE001
        pass                                    # non frozen / non presente


class Splash:
    """Finestra di avvio senza titlebar, trascinabile, con progress bar."""

    def __init__(self) -> None:
        self.root: tk.Tk | None = None
        self._bar: ttk.Progressbar | None = None
        self._status: tk.StringVar | None = None
        self._img = None
        self._drag = (0, 0)
        self._closed = False

    # ------------------------------------------------------------------ #
    def show(self) -> None:
        # tk.Tk() si auto-nomina "root di default". Se lo rimanesse, le
        # StringVar dell'App (create senza master) finirebbero sull'interp
        # dello splash: campi vuoti e "Too early to create variable" nei
        # dialog. Lo splash NON e' root di default, quindi qui ripristino
        # il valore precedente (di solito None).
        prev_root = tk._default_root
        root = tk.Tk()
        tk._default_root = prev_root
        self.root = root
        root.title("IACallMinute — avvio")
        root.overrideredirect(True)            # nessuna titlebar di sistema
        root.configure(bg=BG)
        root.attributes("-topmost", True)      # sopra le altre finestre
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")

        if _ASSET.is_file():
            self._img = tk.PhotoImage(file=str(_ASSET), master=root)
            tk.Label(root, image=self._img, bg=BG).place(
                x=0, y=0, width=W, height=H)
        else:
            tk.Frame(root, bg=BG, width=W, height=H).pack()

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Splash.Horizontal.TProgressbar",
                        troughcolor=TRACK, background=ACCENT,
                        bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT,
                        thickness=BAR["height"])
        self._bar = ttk.Progressbar(
            root, style="Splash.Horizontal.TProgressbar",
            mode="determinate", maximum=100, value=0)
        self._bar.place(**BAR)

        self._status = tk.StringVar(master=root, value="Avvio…")
        tk.Label(root, textvariable=self._status, bg=BG, fg=MUTED,
                 font=("Segoe UI", 10), anchor="w").place(
                     x=52, y=254, width=W - 104, height=20)

        # trascinamento: nessuna titlebar, quindi tutto il corpo e' la maniglia
        root.bind_all("<ButtonPress-1>", self._on_press, add="+")
        root.bind_all("<B1-Motion>", self._on_move, add="+")

        root.update_idletasks()
        root.update()
        log.info("splash mostrato (%dx%d)", W, H)

    # ------------------------------------------------------------------ #
    def set(self, text: str, value: float | None = None) -> None:
        """Aggiorna lo stato; value=None passa la barra in indeterminata."""
        if self._closed or self.root is None or self._bar is None:
            return
        try:
            self._status.set(text)
            mode = str(self._bar.cget("mode"))
            if value is None:
                if mode != "indeterminate":
                    self._bar.stop()
                    self._bar.configure(mode="indeterminate")
                    self._bar.start(14)
            else:
                if mode != "determinate":
                    self._bar.stop()
                    self._bar.configure(mode="determinate")
                self._bar["value"] = value
            self.root.update_idletasks()
        except tk.TclError:
            self._closed = True

    def spin(self, text: str, thread, poll: float = 0.05) -> None:
        """Pompa gli eventi Tk finche thread e' vivo (la finestra resta
        visibile e trascinabile durante il caricamento)."""
        self.set(text, None)
        while thread.is_alive():
            if self._closed or self.root is None:
                return
            try:
                self.root.update()
            except tk.TclError:
                self._closed = True
                return
            time.sleep(poll)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.root is not None:
                self.root.destroy()
        except tk.TclError:
            pass
        self.root = None
        log.info("splash chiuso")

    # ------------------------------------------------------------------ #
    def _on_press(self, event) -> None:
        if self.root is None:
            return
        self._drag = (event.x_root - self.root.winfo_rootx(),
                      event.y_root - self.root.winfo_rooty())

    def _on_move(self, event) -> None:
        if self.root is None:
            return
        x = max(0, event.x_root - self._drag[0])
        y = max(0, event.y_root - self._drag[1])
        try:
            self.root.geometry(f"+{x}+{y}")
        except tk.TclError:
            self._closed = True
