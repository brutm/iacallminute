"""Avvio processi figli senza finestre di console (Windows).

Su Windows ogni subprocess predefinito puo' aprire una breve finestra
cmd nera (ffmpeg, powershell...): con CREATE_NO_WINDOW il processo figlio
nasce senza finestra. Su altri sistemi e' un pass-through.
"""
from __future__ import annotations

import subprocess
import sys

_NO_WIN = 0x08000000 if sys.platform == "win32" else 0


def run(*args, **kwargs):
    if _NO_WIN:
        kwargs.setdefault("creationflags", _NO_WIN)
    return subprocess.run(*args, **kwargs)


def popen(*args, **kwargs):
    if _NO_WIN:
        kwargs.setdefault("creationflags", _NO_WIN)
    return subprocess.Popen(*args, **kwargs)
