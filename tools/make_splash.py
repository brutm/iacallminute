"""Genera assets/splash.png (schermata di avvio dell'eseguibile).

Usato da build_exe.bat prima di PyInstaller: con --splash la finestra di
avvio appare PRIMA dell'estrazione dei file (onefile), così l'utente vede
subito qualcosa invece di un desktop vuoto.

Testo in basso a sinistra: PyInstaller ci sovrappone lo stato di avanzamento
(text_pos nello spec), quindi quell'area resta libera e ad alto contrasto.

    python tools/make_splash.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 560, 320
BG = (15, 23, 42)          # #0f172a, tema scuro dell'app
CARD = (30, 41, 59)        # #1e293b
ACCENT = (56, 189, 248)    # #38bdf8
MUTED = (148, 163, 184)    # #94a3b8
INK = (248, 250, 252)      # #f8fafc

FONTS = [
    ("seguibl.ttf", 40),      # Segoe UI Bold
    ("arialbd.ttf", 40),
    ("segoeuib.ttf", 40),
]
FONT_SUB = [("segoeui.ttf", 16), ("arial.ttf", 16)]
FONT_SMALL = [("segoeui.ttf", 13), ("arial.ttf", 13)]


def _font(candidates: list[tuple[str, int]]) -> ImageFont.FreeTypeFont:
    for name, size in candidates:
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    out = Path(__file__).resolve().parents[1] / "assets" / "splash.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # pannello interno (spazio in basso libero per barra e stato: li
    # disegna app/splash.py sopra l'immagine)
    d.rounded_rectangle([24, 24, W - 24, H - 30], radius=14, fill=CARD)

    # titolo con pallino accent
    f_title = _font(FONTS)
    d.ellipse([52, 54, 68, 70], fill=ACCENT)
    d.text((80, 46), "IACallMinute", font=f_title, fill=INK)

    # sottotitolo
    f_sub = _font(FONT_SUB)
    d.text((80, 100), "trascrizione live di Teams / Zoom con AI",
           font=f_sub, fill=MUTED)

    # riga accent
    d.rectangle([52, 138, 200, 142], fill=ACCENT)

    # decorazione: waveform stilizzato (accent + neutro)
    heights = [10, 26, 16, 40, 22, 54, 30, 18, 46, 24, 12, 34, 20, 44, 28,
               14, 38, 22, 50, 26, 16, 32, 20, 12, 42, 24, 18, 36, 28, 10]
    for i, h in enumerate(heights):
        x = 54 + i * 12
        color = ACCENT if i % 4 == 0 else (71, 85, 105)
        d.rounded_rectangle([x, 178 - h // 2, x + 6, 178 + h // 2],
                            radius=3, fill=color)

    # riga 232-242 e testo 252+ : occupati dalla progress bar e dallo stato
    # dinamici della finestra di app/splash.py

    img.save(out, "PNG", optimize=True)
    print(f"splash scritto: {out} ({out.stat().st_size} byte)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
