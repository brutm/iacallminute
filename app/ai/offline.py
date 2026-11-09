"""Trascrizione offline con faster-whisper + traduzione Argos.

Nessuna quota e nessuna chiave API: i modelli si scaricano una prima
volta (HuggingFace per Whisper, argospm per la traduzione EN/FR/DE/ES->
italiano) e poi funzionano completamente senza rete.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .. import config
from .errors import ProviderError

log = logging.getLogger("iacm.ai.offline")

_TARGET_RATE = 16000
_models: dict = {}
_models_lk = threading.Lock()
# faster-whisper NON e' thread-safe: due worker che trascrivono in
# parallelo sulla stessa istanza corrompono i risultati (la seconda
# sessione restava senza trascrizione). Le inferenze vengono serializzate.
_infer_lk = threading.Lock()
_tr_lk = threading.Lock()
_pairs_done: set = set()
# Modelli su GPU (ctranslate2): crashano all'exit se ancora caricati mentre
# l'interprete muore (0xC0000409) — vengono liberati mentre e' ancora vivo.
_exit_clean = False


def _cleanup_models_at_exit() -> None:
    global _exit_clean
    if _exit_clean:
        return
    _exit_clean = True
    import atexit
    atexit.register(_models.clear)


def release_all() -> None:
    """Libera i modelli Whisper (anche della GPU) a interprete vivo."""
    with _models_lk:
        _models.clear()


# Ultimo dispositivo effettivamente usato da Whisper ("GPU"/"CPU"): per
# l'indicatore nella barra di stato. Vuoto finche' non trascrive nulla.
_active_dev = ""


def last_device() -> str:
    return _active_dev


_cuda_dl_busy = False


def _maybe_ensure_cuda_dlls() -> None:
    """GPU presente ma DLL CUDA assenti: scarico una volta in background.

    Come per i modelli Whisper: dal prossimo caricamento la sessione
    parte in GPU; nel frattempo la trascrizione resta su CPU.
    """
    global _cuda_dl_busy
    if _cuda_dl_busy:
        return
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return
    except Exception:                   # noqa: BLE001
        return
    from . import local_models
    if local_models.cuda_dll_present():
        return
    _cuda_dl_busy = True
    log.info("GPU senza DLL: scarico le librerie CUDA in background "
             "(~1.8 GB, una volta sola)…")

    def _done(ok: bool, msg: str) -> None:
        global _cuda_dl_busy
        _cuda_dl_busy = False
        log.info("download DLL CUDA: %s — %s",
                 msg, ("prossimo caricamento in GPU"
                       if ok else "la trascrizione resta su CPU"))
        if ok:
            with _models_lk:
                _models.clear()         # ricarica il prossimo segmento in GPU

    threading.Thread(
        target=lambda: local_models.download_cuda_dlls(on_done=_done),
        daemon=True, name="iacm-cuda-dl-auto").start()

_argos_mods = None        # None = non ancora provato
_argos_lk = threading.Lock()


def _model_name(stt: dict) -> str:
    name = (stt.get("model") or "").strip()
    return name if name in config.OFFLINE_SIZES else config.DEFAULT_OFFLINE_MODEL


# --------------------------------------------------------------------------- #
# Dispositivo di calcolo: GPU se disponibile, altrimenti CPU
# --------------------------------------------------------------------------- #
# ctranslate2 usa l'API Windows LoadLibrary per caricare cublas/cuDNN: la
# cartella va resa visibile TRAMITE PATH (add_dll_directory da solo non
# basta). Le DLL opzionali vivono nella cartella del progetto, cosi' si
# cancellano con essa e l'exe resta leggero:
#   modelli/cuda_dlls/  oppure  cuda_dlls/ accanto all'exe
#   (override: variabile IACM_CUDA_DLLS)
def _cuda_dll_dirs() -> list:
    out: list = []
    env = os.environ.get("IACM_CUDA_DLLS", "").strip()
    cands = []
    if env:
        cands.append(env)
    cands.append(str(Path(sys.executable).resolve().parent / "cuda_dlls"))
    try:
        from . import local_models
        cands.append(str(local_models.MODELS_DIR / "cuda_dlls"))
    except Exception:                   # noqa: BLE001
        pass
    seen = set()
    for c in cands:
        p = Path(c)
        if p.is_dir() and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append(p)
    return out


def add_cuda_dll_dirs() -> list:
    """Rende visibili le DLL CUDA opzionali; ritorna quelle aggiunte.

    Il loader Windows costruisce la search-path all'avvio del processo:
    cambiare PATH runtime non basta. Si usa SetDllDirectoryW (aggiorna la
    ricerca delle LoadLibrary successive, anche transitiva) e le DLL
    vengono inoltre pre-caricate col percorso completo.
    """
    dirs = _cuda_dll_dirs()
    added = []
    for i, d in enumerate(dirs):
        try:
            os.add_dll_directory(str(d))
        except Exception:               # noqa: BLE001
            pass
        if i == 0:
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(str(d))
            except Exception:           # noqa: BLE001
                pass
        # pre-caricamento esplicito: per ctranslate2 la DLL risulta
        # gia' nel processo e non cerca nulla
        for dll in d.glob("*.dll"):
            try:
                ctypes.WinDLL(str(dll))
            except OSError:
                pass
        added.append(str(d))
    return added


def wanted_device(stt: dict) -> str:
    """«auto» / «gpu» / «cpu» dai settings (default auto)."""
    return ((stt.get("device") or "auto").strip().lower() or "auto")


def gpu_info() -> str:
    """Stato GPU breve, per la finestra Configurazione."""
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
    except Exception as exc:            # noqa: BLE001
        return f"GPU: non rilevata ({exc}) - uso CPU"
    if n == 0:
        return "GPU: nessuna scheda CUDA visibile - uso CPU"
    name = ""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3).stdout.strip()
        name = out.splitlines()[0].strip() if out else ""
    except Exception:                   # noqa: BLE001
        name = ""
    if _cuda_dll_dirs():
        note = "DLL presenti in " + str(_cuda_dll_dirs()[0])
    else:
        note = "DLL CUDA assenti nella cartella modelli: uso CPU"
    return f"GPU: {name or 'scheda CUDA rilevata'} - {note}"


def _probe(m):
    """Inferenza minima: le DLL mancanti esplodono qui, non al primo segmento."""
    t = np.arange(_TARGET_RATE, dtype=np.float64) / _TARGET_RATE
    tone = (np.sin(2 * np.pi * 440.0 * t) * 8000).astype(np.float32)
    segs, _ = m.transcribe(tone, language=None, beam_size=1)
    for _ in segs:
        pass


def _try_cuda(path):
    """Carica il modello con compute realizzato via CUDA.

    Ritorna (modello, None) oppure (None, motivo del fallimento).
    """
    add_cuda_dll_dirs()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return None, "nessuna GPU CUDA visibile"
        from faster_whisper import WhisperModel
        m = WhisperModel(str(path), device="cuda", compute_type="float16")
        _probe(m)
        return m, None
    except Exception as exc:            # noqa: BLE001
        return None, str(exc).strip()[:200]


def _whisper(name: str, stt: dict | None = None):
    global _active_dev
    dev = wanted_device(stt or {})
    key = f"{name}@{dev}"
    from . import local_models
    path = local_models.whisper_path(name)
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ProviderError(
            "trascrizione offline non disponibile: pacchetto "
            "'faster-whisper' non installato (pip install faster-whisper)",
            0, False) from exc
    with _models_lk:
        m = _models.get(key)
        if m is None:
            local_models.ensure_dirs()
            if not path.is_dir():
                # prima volta: download dentro modelli/whisper/<nome>
                try:
                    from huggingface_hub import snapshot_download
                except ImportError as exc:
                    raise ProviderError(
                        f"modello Whisper '{name}' assente: scaricalo da "
                        "Configurazione → Modelli locali", 0, False) from exc
                log.info("download modello Whisper '%s' in %s", name, path)
                try:
                    snapshot_download(repo_id=f"Systran/faster-whisper-{name}",
                                      local_dir=str(path), tqdm_class=None)
                except Exception as exc:            # noqa: BLE001
                    raise ProviderError(
                        f"download del modello Whisper '{name}' fallito: "
                        f"{exc} (Configurazione → Modelli locali)", 0,
                        False) from exc
            log.info("caricamento modello Whisper '%s' da %s (device=%s)",
                     name, path, dev)
            m = None
            if dev in ("gpu", "auto"):
                m, err = _try_cuda(path)
                if m is not None:
                    _active_dev = "GPU"
                    log.info("Whisper usa la GPU (float16)")
                else:
                    _active_dev = "CPU"
                    if dev == "gpu":
                        _maybe_ensure_cuda_dlls()
                        log.warning("GPU richiesta ma non disponibile (%s): "
                                    "uso la CPU", err)
                    else:
                        if "cublas64_12.dll" in (err or ""):
                            _maybe_ensure_cuda_dlls()
                        log.info("GPU non disponibile (%s): uso la CPU",
                                 (err or "DLL CUDA assenti")[:80])
            if m is None:
                _active_dev = "CPU"
                try:
                    m = WhisperModel(str(path), device="cpu",
                                     compute_type="int8")
                except Exception as exc:            # noqa: BLE001
                    raise ProviderError(
                        f"modello Whisper '{name}' non caricato: {exc}",
                        0, False) from exc
            _models[key] = m
    _cleanup_models_at_exit()
    return m


def _wav_f32(wav_bytes: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except wave.Error as exc:
        raise ProviderError(f"WAV non leggibile: {exc}", 0, False) from exc
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ProviderError(f"ampiezza WAV {sw * 8} bit non supportata",
                            0, False)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate and rate != _TARGET_RATE and len(a):
        n = max(1, int(round(len(a) * _TARGET_RATE / rate)))
        a = np.interp(np.linspace(0.0, len(a) - 1, n),
                      np.arange(len(a), dtype=np.float64), a)
        a = a.astype(np.float32)
    return np.ascontiguousarray(a)


def _argos_setup():
    """Importa Argos Translate e imposta lo splitter di frasi a MiniSBD.

    Ritorna (package, translate) oppure None se Argos non e' installato.

    I pacchetti di traduzione (es. en_it) contengono i modelli Stanza, ma
    Stanza richiede torch (~500 MB) che e' escluso dal bundle: senza questo
    settaggio la traduzione cade in silenzio con "'NoneType' object has no
    attribute 'Pipeline'". MiniSBD pesa 0,18 MB per lingua, basta per le
    frasi brevi dei segmenti e fa anche risparmiare il caricamento Stanza
    (~30 s al primo utilizzo).
    """
    global _argos_mods
    if _argos_mods is not None:
        return _argos_mods or None
    with _argos_lk:
        if _argos_mods is None:
            try:
                # i pacchetti Argos vivono in modelli/argos accanto all'app:
                # la cartella va indicata PRIMA dell'import
                from . import local_models
                local_models.ensure_dirs()
                local_models.set_argos_env()
                from argostranslate import package as argos_pkg
                from argostranslate import settings as argos_settings
                from argostranslate import translate as argos_tr
            except ImportError:
                _argos_mods = ()
            else:
                try:
                    argos_settings.chunk_type = argos_settings.ChunkType.MINISBD
                except Exception:                     # noqa: BLE001
                    log.debug("chunk_type MinisBD non impostabile")
                _argos_mods = (argos_pkg, argos_tr)
        return _argos_mods or None


def _translate(text: str, src: str) -> str:
    """Traduzione offline verso l'italiano (Argos); '' se non disponibile.

    Al primo uso scarica e installa il pacchetto lingua->it (serve la rete
    una volta sola).
    """
    src = (src or "").strip().lower()[:2]
    if not text or src in ("", "it"):
        return ""
    mods = _argos_setup()
    if mods is None:
        return ""
    argos_pkg, argos_tr = mods
    pair = (src, "it")
    try:
        with _tr_lk:
            if pair not in _pairs_done:
                have = {l.code for l in argos_tr.get_installed_languages()}
                if src not in have:
                    try:
                        argos_pkg.update_package_index()
                        cand = next(
                            (p for p in argos_pkg.get_available_packages()
                             if p.from_code == src and p.to_code == "it"),
                            None)
                        if cand is None:
                            log.info("nessun pacchetto traduzione %s->it", src)
                            return ""
                        argos_pkg.install_from_path(cand.download())
                        log.info("pacchetto traduzione %s->it installato", src)
                    except Exception as exc:
                        # non marcare come fatto: la prossima chiamata
                        # riprova l'installazione
                        log.warning(
                            "installazione traduzione %s->it fallita: %s",
                            src, exc)
                        return ""
                _pairs_done.add(pair)
        langs = argos_tr.get_installed_languages()
        from_l = next((l for l in langs if l.code == src), None)
        to_l = next((l for l in langs if l.code == "it"), None)
        if from_l is None or to_l is None:
            return ""
        out = from_l.get_translation(to_l).translate(text)
        return (out or "").strip()
    except Exception as exc:
        log.warning("traduzione offline fallita: %s", exc)
        return ""


def transcribe(stt: dict, wav_bytes: bytes, *,
               duration: float, start: float, bilingual: bool,
               language: str = "auto") -> dict:
    """Trascrive con Whisper locale; traduce in italiano con Argos.

    ``language``: "auto" (rilevamento) oppure codice ISO forzato (es. "it"):
    con la lingua forzata Whisper non puo' sbagliare riconoscimento (il
    parlato italiano etichettato "es" veniva poi tradotto in spagnolo).
    """
    name = _model_name(stt)
    model = _whisper(name, stt)
    audio = _wav_f32(wav_bytes)
    if not len(audio):
        return {"language": "", "italian": "", "original": ""}
    forced = (language or "auto").strip().lower()
    t0 = time.monotonic()
    try:
        with _infer_lk:                     # inferenza serializzata
            segments, info = model.transcribe(
                audio,
                language=None if forced in ("", "auto")
                else _iso(forced),
                vad_filter=True, beam_size=1,
                condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
            lang = (info.language or "").strip()
    except Exception as exc:                        # noqa: BLE001
        raise ProviderError(f"Whisper locale fallito: {exc}", 0, False) from exc
    if forced in ("", "auto"):
        forced = ""
    else:
        # con la lingua imposta, faster-whisper talvolta riporta il codice
        # "grezzo" della sua tabella: si normalizza su quanto richiesto
        lang = _iso(forced)
    log.info("stt offline: model=%s lang=%s (forzata=%s) dur=%.1fs t=%.1fs "
             "bilingual=%s", name, lang, forced or "auto", duration,
             time.monotonic() - t0, bilingual)
    if not text:
        return {"language": lang, "italian": "", "original": ""}
    if lang.startswith("it"):
        return {"language": "it", "italian": text, "original": ""}
    if bilingual:
        it = _translate(text, lang)
        if it:
            return {"language": lang, "italian": it, "original": text}
        return {"language": lang, "italian": "", "original": text}
    it = _translate(text, lang)
    return {"language": lang, "italian": it or text, "original": ""}


def _iso(code: str) -> str | None:
    """Normalizza il codice lingua al formato ISO-639-1 di Whisper."""
    code = (code or "").strip().lower()[:2]
    return code or None