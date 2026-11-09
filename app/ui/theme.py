"""Tema e palette condivisi per le finestre di IACallMinute."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#f1f5f9"          # sfondo generale
CARD = "#ffffff"        # schede
INK = "#0f172a"         # testo principale
MUTED = "#64748b"       # testo secondario
BORDER = "#e2e8f0"
HEADER = "#0f172a"      # barra titolo
BLUE = "#2563eb"
BLUE_D = "#1d4ed8"
GREEN = "#16a34a"
GREEN_D = "#15803d"
RED = "#dc2626"
RED_D = "#b91c1c"
AMBER = "#d97706"
TRACK = "#e2e8f0"

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
FONT_MONO = ("Consolas", 9)
FONT_HEADER = ("Segoe UI Semibold", 15)
FONT_SMALL = ("Segoe UI", 9)


def apply_style(root: tk.Tk) -> ttk.Style:
    """Tema clam: leggero, performante, permette i colori custom."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)

    style.configure(".", font=FONT, background=BG, foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Header.TFrame", background=HEADER)

    style.configure("TLabel", background=CARD, foreground=INK, font=FONT)
    style.configure("Bg.TLabel", background=BG, foreground=INK, font=FONT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=FONT_SMALL)
    style.configure("MutedBg.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("Header.TLabel", background=HEADER, foreground="#f8fafc",
                    font=FONT_HEADER)
    style.configure("HeaderSub.TLabel", background=HEADER, foreground="#94a3b8",
                    font=FONT_SMALL)
    style.configure("Mono.TLabel", background=CARD, foreground="#334155",
                    font=FONT_MONO)
    style.configure("MonoBg.TLabel", background=BG, foreground="#334155",
                    font=FONT_MONO)

    style.configure("Card.TLabelframe", background=BG, foreground=INK,
                    bordercolor=BORDER, borderwidth=1, relief="solid")
    style.configure("Card.TLabelframe.Label", background=BG, foreground=BLUE,
                    font=("Segoe UI Semibold", 10))

    style.configure("TButton", font=FONT, padding=(10, 5), borderwidth=0,
                    focusthickness=0, relief="flat", background="#e2e8f0",
                    foreground=INK)
    style.map("TButton",
              background=[("active", "#cbd5e1"), ("disabled", "#f1f5f9")],
              foreground=[("disabled", "#94a3b8")])

    style.configure("Success.TButton", background=GREEN, foreground="white",
                    font=("Segoe UI Semibold", 11), padding=(16, 8))
    style.map("Success.TButton",
              background=[("active", GREEN_D), ("disabled", "#94a3b8")],
              foreground=[("disabled", "#e2e8f0")])

    style.configure("Danger.TButton", background=RED, foreground="white",
                    font=("Segoe UI Semibold", 11), padding=(16, 8))
    style.map("Danger.TButton",
              background=[("active", RED_D), ("disabled", "#fca5a5")],
              foreground=[("disabled", "#fee2e2")])

    style.configure("Accent.TButton", background=BLUE, foreground="white",
                    font=("Segoe UI Semibold", 10), padding=(14, 7))
    style.map("Accent.TButton",
              background=[("active", BLUE_D), ("disabled", "#93c5fd")],
              foreground=[("disabled", "#eff6ff")])

    style.configure("TCheckbutton", background=CARD, foreground=INK, font=FONT)
    style.map("TCheckbutton", background=[("active", CARD)],
              foreground=[("disabled", "#94a3b8")])
    style.configure("Bg.TCheckbutton", background=BG, foreground=INK, font=FONT)
    style.map("Bg.TCheckbutton", background=[("active", BG)],
              foreground=[("disabled", "#94a3b8")])

    style.configure("TEntry", fieldbackground=CARD, foreground=INK,
                    bordercolor=BORDER, lightcolor=BORDER, padding=4)
    style.map("TEntry", bordercolor=[("focus", BLUE)], lightcolor=[("focus", BLUE)])

    style.configure("TCombobox", fieldbackground=CARD, foreground=INK,
                    bordercolor=BORDER, lightcolor=BORDER, padding=4,
                    arrowcolor=MUTED)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD), ("disabled", "#f8fafc")],
              foreground=[("disabled", "#94a3b8")])

    style.configure("TSpinbox", fieldbackground=CARD, foreground=INK,
                    bordercolor=BORDER, lightcolor=BORDER, padding=4,
                    arrowcolor=MUTED, arrowsize=12)
    style.map("TSpinbox", bordercolor=[("focus", BLUE)],
              lightcolor=[("focus", BLUE)],
              fieldbackground=[("disabled", "#f8fafc")],
              foreground=[("disabled", "#94a3b8")])

    style.configure("Horizontal.TProgressbar", troughcolor=TRACK,
                    background=GREEN, bordercolor=TRACK, lightcolor=GREEN,
                    darkcolor=GREEN, thickness=8)
    # indicatore livello audio: verde → ambra → rosso (saturazione)
    for name, col in (("LevelOk", GREEN), ("LevelWarn", AMBER),
                      ("LevelHot", RED)):
        style.configure(f"{name}.Horizontal.TProgressbar", troughcolor=TRACK,
                        background=col, bordercolor=TRACK, lightcolor=col,
                        darkcolor=col, thickness=8)

    style.configure("Vertical.TScrollbar", background="#cbd5e1",
                    troughcolor=BG, bordercolor=BG, arrowcolor=MUTED)
    style.map("Vertical.TScrollbar",
              background=[("active", "#94a3b8"), ("pressed", "#64748b")])
    style.configure("TSeparator", background=BORDER)

    # tendina delle combobox coerente con il tema
    root.option_add("*TCombobox*Listbox.font", FONT)
    root.option_add("*TCombobox*Listbox.selectBackground", BLUE)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    return style
