"""Rileva la fine di una chiamata per fermare da sola la registrazione.

Teams, Zoom, Meet nel browser … tengono aperto il microfono (una sessione
audio di cattura WASAPI «attiva») per tutta la chiamata, anche se ci si
silenzia nell'app, e la chiudono quando la chiamata finisce: è il segnale
«chiamata terminata». Le sessioni si leggono con l'API pubblica di Windows
(IAudioSessionManager2 sui dispositivi di cattura), senza permessi admin.

Rete di sicurezza: se nessuna app di chiamata viene mai vista, la sessione
si ferma dopo ``SILENCE_S`` di silenzio totale (solo dopo aver sentito
audio almeno una volta, per non fermarsi prima che la call cominci).

``CallWatch`` è logica pura (testabile); ``mic_apps_in_use`` interroga
Windows (va chiamata da un thread di lavoro: inizializza COM in MTA, cosa
da non fare nel thread di Tk).
"""
from __future__ import annotations

import ctypes
import os
import threading

POLL_S = 3.0          # ogni quanto si controllano le sessioni audio
CONFIRM_S = 30.0      # microfono rilasciato per tanto: chiamata finita
SILENCE_S = 5 * 60.0  # fallback senza app di chiamata riconosciuta
IDLE_S = 5 * 60.0     # app ancora col microfono ma audio assente da tanto
SHOW_IDLE_FOR = 120.0 # countdown mostrato a schermo negli ultimi 2 min

# sottostringhe (minuscole) del nome dell'eseguibile delle app di chiamata
CALL_APPS: tuple[tuple[str, str], ...] = (
    ("teams", "Teams"),
    ("zoom", "Zoom"), ("webex", "Webex"), ("ciscocollab", "Webex"),
    ("atmgr", "Webex"),
    ("skype", "Skype"), ("slack", "Slack"), ("discord", "Discord"),
    ("whatsapp", "WhatsApp"), ("telegram", "Telegram"),
    ("signal", "Signal"), ("gotomeeting", "GoTo"), ("g2mcomm", "GoTo"),
    ("ringcentral", "RingCentral"),
    ("chrome.exe", "Chrome"), ("msedge.exe", "Edge"),
    ("msedgewebview2.exe", "Edge"),
    ("firefox.exe", "Firefox"), ("brave.exe", "Brave"),
    ("opera.exe", "Opera"), ("vivaldi.exe", "Vivaldi"),
)


def call_app_name(exe: str) -> str | None:
    """Nome leggibile se l'eseguibile è di un'app di chiamata."""
    low = os.path.basename(exe.replace("#", "\\")).lower()
    for needle, label in CALL_APPS:
        if needle in low:
            return label
    return None


# --- sessioni audio di cattura (WASAPI via COM, solo ctypes) ---------------
_CLSCTX_ALL = 0x17
_E_CHANGED_MODE = -2147417850          # RPC_E_CHANGED_MODE
_SESSION_ACTIVE = 1                    # AudioSessionStateActive


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str) -> None:
        super().__init__()
        h = text.replace("-", "")
        self.d1 = int(h[0:8], 16)
        self.d2 = int(h[8:12], 16)
        self.d3 = int(h[12:16], 16)
        for i in range(8):
            self.d4[i] = int(h[16 + 2 * i:18 + 2 * i], 16)


_CLSID_ENUM = "BCDE0395-E52F-467C-8E3D-C4579291692E"   # MMDeviceEnumerator
_IID_ENUM = "A95664D2-9614-4F35-A746-DE8DB63617E6"     # IMMDeviceEnumerator
_IID_MGR2 = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"     # IAudioSessionManager2
_IID_CTL2 = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"     # IAudioSessionControl2

_PP = ctypes.POINTER(ctypes.c_void_p)


def _vcall(obj: ctypes.c_void_p, index: int, argtypes: tuple, *args) -> int:
    """Chiama il metodo ``index`` della vtable COM (solleva OSError se fallisce)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(_PP)).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj and obj.value:
        vtbl = ctypes.cast(obj, ctypes.POINTER(_PP)).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)
        obj.value = None


def _exe_path(pid: int) -> str:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    k32.QueryFullProcessImageNameW.argtypes = (
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong))
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    h = k32.OpenProcess(0x1000, 0, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = ctypes.c_ulong(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def mic_apps_in_use() -> set[str] | None:
    """Eseguibili (percorso) con una sessione di cattura audio attiva.

    Esclude questo processo. None se Windows non risponde (o non è Windows).
    Da chiamare fuori dal thread di Tk (inizializza COM in MTA).
    """
    if os.name != "nt":
        return None
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole32.CoCreateInstance.restype = ctypes.c_long
    hr = ole32.CoInitializeEx(None, 0)            # COINIT_MULTITHREADED
    if hr < 0 and hr != _E_CHANGED_MODE:
        return None
    uninit = hr >= 0
    held: list[ctypes.c_void_p] = []

    def new() -> ctypes.c_void_p:
        p = ctypes.c_void_p()
        held.append(p)
        return p

    found: set[str] = set()
    me = os.getpid()
    try:
        enum = new()
        if ole32.CoCreateInstance(
                ctypes.byref(_GUID(_CLSID_ENUM)), None, _CLSCTX_ALL,
                ctypes.byref(_GUID(_IID_ENUM)), ctypes.byref(enum)) < 0:
            return None
        coll = new()
        # EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE)
        _vcall(enum, 3, (ctypes.c_int, ctypes.c_uint, _PP), 1, 1,
               ctypes.byref(coll))
        n = ctypes.c_uint()
        _vcall(coll, 3, (ctypes.POINTER(ctypes.c_uint),), ctypes.byref(n))
        for i in range(n.value):
            try:
                dev = new()
                _vcall(coll, 4, (ctypes.c_uint, _PP), i, ctypes.byref(dev))
                mgr = new()
                _vcall(dev, 3, (ctypes.POINTER(_GUID), ctypes.c_uint,
                                ctypes.c_void_p, _PP),
                       ctypes.byref(_GUID(_IID_MGR2)), _CLSCTX_ALL, None,
                       ctypes.byref(mgr))
                sessions = new()
                _vcall(mgr, 5, (_PP,), ctypes.byref(sessions))
                cnt = ctypes.c_int()
                _vcall(sessions, 3, (ctypes.POINTER(ctypes.c_int),),
                       ctypes.byref(cnt))
                for j in range(cnt.value):
                    ctl = new()
                    _vcall(sessions, 4, (ctypes.c_int, _PP), j,
                           ctypes.byref(ctl))
                    state = ctypes.c_int()
                    _vcall(ctl, 3, (ctypes.POINTER(ctypes.c_int),),
                           ctypes.byref(state))
                    if state.value != _SESSION_ACTIVE:
                        continue
                    ctl2 = new()
                    _vcall(ctl, 0, (ctypes.POINTER(_GUID), _PP),
                           ctypes.byref(_GUID(_IID_CTL2)),
                           ctypes.byref(ctl2))
                    pid = ctypes.c_ulong()
                    _vcall(ctl2, 14, (ctypes.POINTER(ctypes.c_ulong),),
                           ctypes.byref(pid))
                    if pid.value and pid.value != me:
                        found.add(_exe_path(pid.value) or f"pid {pid.value}")
            except OSError:
                continue          # dispositivo sparito / sessione chiusa
        return found
    except OSError:
        return None
    finally:
        for p in reversed(held):
            try:
                _release(p)
            except OSError:
                pass
        if uninit:
            ole32.CoUninitialize()


def call_apps_in_use() -> set[str] | None:
    """Nomi leggibili delle app di chiamata col microfono aperto."""
    apps = mic_apps_in_use()
    if apps is None:
        return None
    return {n for n in (call_app_name(a) for a in apps) if n}


class MicProbe:
    """Thread che interroga Windows ogni ``interval`` secondi.

    ``latest``: ultimo risultato di ``call_apps_in_use`` (None = ignoto);
    ``checked``: True dopo la prima interrogazione.
    """

    def __init__(self, interval: float = POLL_S) -> None:
        self.interval = interval
        self.latest: set[str] | None = None
        self.checked = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="callwatch-probe")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                apps = call_apps_in_use()
            except Exception:          # difesa: mai far morire il thread
                apps = None
            self.latest = apps
            self.checked = True
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


class CallWatch:
    """Macchina a stati: decide quando la chiamata è finita.

    ``poll(apps, now)`` (``apps`` None = stato ignoto) restituisce un evento
    o None:
      - ``"call"``: chiamata rilevata (prima volta);
      - ``"gone"``: microfono rilasciato, attendo conferma;
      - ``"back"``: chiamata ripresa entro la conferma;
      - ``"end"``: chiamata finita (rilasciato da ``confirm_s``);
      - ``"idle"``: app di chiamata ancora col microfono aperto ma nessun
        audio da ``idle_s`` (es. Webex, che NON rilascia il microfono alla
        fine della call e lo tiene aperto finché l'app è attiva);
      - ``"silence"``: nessuna app vista e silenzio da ``silence_s``.
    """

    def __init__(self, confirm_s: float = CONFIRM_S,
                 silence_s: float = SILENCE_S,
                 idle_s: float = IDLE_S) -> None:
        self.confirm_s = confirm_s
        self.silence_s = silence_s
        self.idle_s = idle_s
        self.seen_call = False
        self.apps: set[str] = set()
        self.gone_since: float | None = None
        self.last_sound: float | None = None
        self.done = False

    def sound(self, now: float) -> None:
        self.last_sound = now

    def remaining(self, now: float) -> float | None:
        """Secondi allo stop durante l'attesa di conferma/idle."""
        if self.gone_since is not None:
            return max(0.0, self.confirm_s - (now - self.gone_since))
        # idle: mostro il conto solo negli ultimi SHOW_IDLE_FOR secondi,
        # per non sparare 「stop tra 5 min」 per tutta la riunione
        if (self.last_sound is not None
                and now - self.last_sound >= self.idle_s - SHOW_IDLE_FOR):
            return max(0.0, self.idle_s - (now - self.last_sound))

    def poll(self, apps: set[str] | None, now: float) -> str | None:
        if self.done:
            return None
        if apps is None and self.seen_call:
            return None                  # stato ignoto: non decidere
        if apps:
            self.apps = set(apps)
            if not self.seen_call:
                self.seen_call = True
                return "call"
            if self.gone_since is not None:
                self.gone_since = None
                return "back"
            # app di chiamata presente ma nessun audio da idle_s: o la call
            # è finita da un pezzo (Webex non rilascia il mic) o la riunione
            # è muta da troppo: fermo lo stesso
            if (self.last_sound is not None
                    and now - self.last_sound >= self.idle_s):
                self.done = True
                return "idle"
            return None
        if self.seen_call:
            if self.gone_since is None:
                self.gone_since = now
                return "gone"
            if now - self.gone_since >= self.confirm_s:
                self.done = True
                return "end"
            return None
        if (self.last_sound is not None
                and now - self.last_sound >= self.silence_s):
            self.done = True
            return "silence"
        return None
