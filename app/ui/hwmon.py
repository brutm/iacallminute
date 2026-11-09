"""Carico CPU / GPU / RAM per la barra di stato (leggero, in tempo reale).

Campionamento SENZA thread dedicato: la misura avviene nel ciclo `after`
della UI (main thread). Motivo: un GC che scatta in un thread secondario
puo' raccogliere oggetti Tk creati in un altro thread e Tcl abortisce
("Tcl_AsyncDelete: async handler deleted by the wrong thread") — visto
deterministicamente nel selftest. Le letture (psutil + NVML via ctypes)
costano <1 ms e non bloccano l'interfaccia.

No psutil -> l'indicatore semplicemente non viene mostrato.
"""
from __future__ import annotations

import ctypes

try:
    import psutil
except ImportError:                        # pragma: no cover
    psutil = None


class _NvmlUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvmlMem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class HwMon:
    """Campiona il carico di sistema e lo rende come una stringa breve.

    API compatibile con la precedente versione a thread (start/stop
    divengono no-op): la lettura vera avviene in snapshot(), chiamata
    dalla UI nel thread principale.
    """

    def __init__(self) -> None:
        self._nv = _Nvml()
        self._prime = False                      # baseline presa?
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)   # baseline (primo=0)
                self._prime = True
            except Exception:                       # noqa: BLE001
                pass

    # ---------------------------------------------------------------- API
    def start(self) -> None:                       # compat: nessun thread
        pass

    def stop(self) -> None:                        # compat
        pass

    def stop_wait(self, timeout: float = 0.0) -> None:   # compat
        pass

    def join(self, timeout: float = 0.0) -> None:   # compat
        pass

    def snapshot(self) -> str:
        """Stringa tipo «CPU 12% · GPU 3% · RAM 5,3/16 GB» ("" se assente)."""
        if psutil is None or not self._prime:
            return ""
        try:
            parts: list[str] = []
            cpu = int(psutil.cpu_percent(interval=None))
            parts.append(f"CPU {cpu}%")
            gpu = self._nv.util()
            if gpu is not None:
                parts.append(f"GPU {gpu}%")
            vm = psutil.virtual_memory()
            parts.append(f"RAM {vm.used / 2**30:.1f}/"
                         f"{vm.total / 2**30:.0f} GB")
            return " ".join(parts)
        except Exception:                          # noqa: BLE001
            return ""


class _Nvml:
    """Accesso minimale a NVML (driver NVIDIA) con ctypes.

    Caricamento pigro: se non c'e' driver NVIDIA, resta disattivato e
    la UI mostra solo CPU/RAM — errore assorbito, mai lanciato.
    """

    def __init__(self) -> None:
        self.ok = False
        self._lib = None
        self._handle = ctypes.c_void_p()
        try:
            lib = ctypes.CDLL("nvml.dll")
            if lib.nvmlInit() != 0:
                return
            if lib.nvmlDeviceGetHandleByIndex_v2(
                    0, ctypes.byref(self._handle)) != 0:
                return
            lib.nvmlDeviceGetUtilizationRates.argtypes = (
                ctypes.c_void_p, ctypes.POINTER(_NvmlUtil))
            lib.nvmlDeviceGetUtilizationRates.restype = ctypes.c_int
            self._lib = lib
            self.ok = True
        except Exception:                      # noqa: BLE001  OSError/AttributeError
            self._lib = None
            self.ok = False

    def util(self) -> int | None:
        """% occupazione GPU (0-100) oppure None."""
        if not self.ok:
            return None
        u = _NvmlUtil()
        try:
            if self._lib.nvmlDeviceGetUtilizationRates(
                    self._handle, ctypes.byref(u)) != 0:
                return None
        except Exception:                      # noqa: BLE001
            return None
        return int(u.gpu)
