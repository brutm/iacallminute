"""Utilità Tk: protegge dalle chiamate Tcl fatte fuori dal thread UI."""
from __future__ import annotations

import tkinter as tk


def enable_dpi_awareness() -> None:
    """Testo nitido su schermi con ridimensionamento (125%, 150%…).

    Va chiamata PRIMA di creare qualsiasi finestra Tk. Per-monitor aware
    come fa già mss alla prima cattura: così le coordinate di Tk, di
    GetWindowRect e di mss coincidono (pixel fisici) fin dall'avvio.
    """
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:                       # noqa: BLE001  Win < 8.1
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:                           # noqa: BLE001
        pass


def ui_scale(widget: tk.Misc) -> float:
    """Fattore di scala (1.0 a 96 DPI) per le misure espresse in pixel."""
    try:
        return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
    except Exception:                           # noqa: BLE001
        return 1.0


def detach_tk_vars(widget: tk.Misc) -> None:
    """Stacca tutte le variabili tk (StringVar/BooleanVar/…) di un widget.

    Variable.__del__ chiama Tcl. Se l'oggetto viene raccolto dal GC in un
    thread secondario (perché un worker teneva l'ultimo riferimento a self),
    Tcl rifiuta la chiamata ("main thread is not in main loop") e il
    processo muore con Tcl_AsyncDelete. Azzerando _tk il __del__ diventa un
    no-op: vedi tkinter.Variable.__del__ (riga 412: if self._tk is None: return).
    """
    for obj in vars(widget).values():
        if isinstance(obj, tk.Variable):
            try:
                obj._tk = None
            except Exception:                   # noqa: BLE001
                pass


class SafeDestroy:
    """Mixin per le finestre che possono restare in vita in un thread.

    Stacca le variabili Tk appena la finestra viene distrutta, così anche un
    riferimento residuo tenuto da un worker non puo' far crashare Tcl quando
    il thread rilascia self.
    """

    def destroy(self) -> None:                  # type: ignore[override]
        try:
            detach_tk_vars(self)
        finally:
            super().destroy()
