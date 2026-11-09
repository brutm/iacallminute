"""Single instance: impedisce di avpiu' copie di IACallMinute.

Uso un mutex Win32 con nome globale alla sessione: il sistema lo rilascia
automaticamente quando il processo muore, quindi non resta mai un lock
bloccato dopo un crash.
"""
from __future__ import annotations

import ctypes
import logging
import re

log = logging.getLogger("iacm.singleinstance")

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "IACallMinute_SingleInstance_v1"
# Titolo della finestra principale: "IACallMinute 1.1.0".
# Match esplicito: un semplice startswith catturerebbe anche Esplora file
# quando e' aperta la cartella del progetto ("IACallMinute e 5 altre…").
TITLE_RE = re.compile(r"^IACallMinute \d+\.\d+\.\d+")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_handle: int | None = None


def acquire() -> bool:
    """True se questa e' la prima istanza (mutex acquisito)."""
    global _handle
    if _handle:
        return True
    h = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not h:
        # errore imprevisto: meglio avviare che bloccare l'utente
        log.warning("CreateMutexW fallito (err=%s)", ctypes.get_last_error())
        return True
    _handle = h
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    if already:
        log.info("istanza gia' in esecuzione rilevata")
    return not already


def release() -> None:
    """Rilascia il mutex (normalmente non serve: esce col processo)."""
    global _handle
    if _handle:
        _kernel32.CloseHandle(_handle)
        _handle = None


def focus_existing() -> bool:
    """Porta in primo piano la finestra di un'altra istanza. True se trovata."""
    found: list[int] = []
    ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                  ctypes.c_void_p)

    @ENUMPROC
    def _cb(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(hwnd, buf, 512)
            if TITLE_RE.match(buf.value):
                found.append(int(hwnd))
        except Exception:                       # noqa: BLE001
            pass
        return True

    _user32.EnumWindows(_cb, 0)
    if not found:
        return False
    hwnd = found[0]
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)             # SW_RESTORE
    _user32.SetForegroundWindow(hwnd)
    _user32.BringWindowToTop(hwnd)
    return True


def notify_busy() -> None:
    """Dialogo minimale quando non si riesce a mettere a fuoco l'altra istanza."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "IACallMinute già in esecuzione",
            "Il programma è già aperto: ho cercato di portarlo in primo "
            " piano.\n\nSe non lo vedi, controlla nella barra delle "
            "attività.",
            parent=root)
        root.destroy()
    except Exception:                           # noqa: BLE001
        log.exception("notifica istanza duplicata fallita")


def ensure() -> bool:
    """True se si puo' procedere; altrimenti segnala l'altra istanza ed esce."""
    if acquire():
        return True
    if not focus_existing():
        notify_busy()
    return False


if __name__ == "__main__":                     # smoke test manuale
    print("acquire ->", acquire())
    input("premi Invio per uscire…")
    release()
