"""Impedisce sospensione / screensaver mentre si registra (Windows).

SetThreadExecutionState vale per il thread che lo chiama: va usato sempre
dal thread Tk (avvio e fine sessione). Alla chiusura del processo Windows
annulla comunque la richiesta.
"""
from __future__ import annotations

import ctypes
import logging
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_log = logging.getLogger("iacm.power")
_active: str | None = None


def keep_awake(display: bool) -> bool:
    """Niente sospensione; con ``display`` anche niente schermo spento né
    screensaver (serve alla cattura video). Ritorna True se applicato."""
    global _active
    if sys.platform != "win32":
        return False
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if display:
        flags |= ES_DISPLAY_REQUIRED
    try:
        ok = bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:                               # noqa: BLE001
        ok = False
    if ok:
        _active = "sistema+schermo" if display else "sistema"
        _log.info("blocco sospensione attivo (%s)", _active)
    else:
        _log.warning("blocco sospensione non riuscito")
    return ok


def release() -> None:
    global _active
    if _active is None or sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:                               # noqa: BLE001
        pass
    _log.info("blocco sospensione rilasciato (%s)", _active)
    _active = None


def active() -> str | None:
    return _active
