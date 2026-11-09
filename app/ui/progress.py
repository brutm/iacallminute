"""Finestra «In salvataggio…» con barra di avanzamento e Annulla.

Le operazioni lunghe (mux del video, conversione MP3, salvataggio di un
WAV di gigabyte) girano in un thread separato: questa finestra mostra
l'avanzamento reale e resta reattiva; «Annulla» interrompe ffmpeg senza
lasciare file corrotti.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import CARD, FONT_SMALL, INK, MUTED, BLUE


class SaveProgressDialog(tk.Toplevel):
    """Barra di avanzamento modale; chi si usa regola on_progress/cancel."""

    def __init__(self, parent: tk.Misc, title: str, message: str):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=CARD, padx=22, pady=16)
        self.protocol("WM_DELETE_WINDOW", self.request_cancel)
        self.bind("<Escape>", lambda e: self.request_cancel())

        from .theme import apply_style
        apply_style(self)

        self.on_cancel = None           # callable o None (impostato dal chiamante)
        self.cancelled = False

        tk.Label(self, text=title, bg=CARD, fg=INK,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w")
        self.msg_var = tk.StringVar(value=message)
        tk.Label(self, textvariable=self.msg_var, bg=CARD, fg=MUTED,
                 font=FONT_SMALL, wraplength=360, justify="left").pack(
            anchor="w", pady=(2, 10))

        self.bar = ttk.Progressbar(self, maximum=1.0, length=360,
                                   mode="determinate")
        self.bar.pack(anchor="w")
        self.bar.configure(value=0.0)

        self.pct_lbl = tk.Label(self, text="0%", bg=CARD, fg=BLUE,
                                font=("Segoe UI", 10))
        self.pct_lbl.pack(anchor="e", pady=(4, 10))

        self.cancel_btn = ttk.Button(self, text="Annulla",
                                     command=self.request_cancel)
        self.cancel_btn.pack(anchor="e")

        self._center_on(parent)

    def _center_on(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        try:
            x = parent.winfo_rootx()
            y = parent.winfo_rooty()
            w = parent.winfo_width()
            h = parent.winfo_height()
            self.geometry(f"+{x + (w - self.winfo_width()) // 2}+"
                          f"{y + (h - self.winfo_height()) // 2}")
        except Exception:                       # noqa: BLE001
            pass

    def update_progress(self, frac: float, msg: str | None = None) -> None:
        self.bar.configure(value=max(0.0, min(1.0, frac)))
        self.pct_lbl.configure(text=f"{int(frac * 100)}%")
        if msg:
            self.msg_var.set(msg)

    def request_cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.cancel_btn.configure(state="disabled", text="annullamento…")
        self.pct_lbl.configure(text="annullamento…", fg=MUTED)
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:                   # noqa: BLE001
                pass

    def close(self) -> None:
        try:
            self.destroy()
        except Exception:                       # noqa: BLE001
            pass
