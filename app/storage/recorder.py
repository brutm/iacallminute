"""Gestione del file di registrazione (temporaneo -> salvataggio su richiesta)."""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import wave
from pathlib import Path

from .. import subproc

TEMP_WAV_NAME = "recording.wav"

_COPY_CHUNK = 4 << 20          # 4 MB a blocco (copia con progresso)

log = logging.getLogger("iacm.recorder")


def temp_wav_path(session_dir: Path) -> Path:
    """WAV temporaneo della sessione: sempre creato, salvabile a richiesta."""
    return Path(session_dir) / TEMP_WAV_NAME


def ffmpeg_path() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        from ..ai import local_models
        return local_models.ffmpeg_path()
    except Exception:                       # noqa: BLE001
        return None


def wav_duration(path: Path) -> float:
    """Durata in secondi di un WAV (0 se illeggibile)."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:                           # noqa: BLE001
        return 0.0


def save_recording(temp_wav: Path, dest: Path, *,
                   on_progress=None, cancel_event: threading.Event | None
                   = None) -> dict:
    """Salva la registrazione temporanea al percorso scelto dall'utente.

    estensione .mp3 -> conversione con ffmpeg (con avanzamento reale via
    «-progress pipe:1»); altrimenti copia a blocchi con avanzamento (il
    WAV di una call lunga puo' pesare gigabyte). Con cancel_event settato
    l'operazione si interrompe e si ritorna warning «annullato».
    Il temporaneo NON viene eliminato: si può salvare più volte, finché
    l'app non chiude (poi la sessione temporanea viene rimossa).
    Restituisce {"path": Path|None, "warnings": [str], "cancelled": bool}.
    """
    out: dict = {"path": None, "warnings": [], "cancelled": False}
    temp_wav = Path(temp_wav)
    if not temp_wav.is_file() or temp_wav.stat().st_size == 0:
        out["warnings"].append(
            "registrazione temporanea assente o vuota (salva prima di "
            "chiudere l'app)")
        return out

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.suffix.lower() != ".mp3":
        try:
            _copy_with_progress(temp_wav, dest, on_progress, cancel_event)
            if cancel_event is not None and cancel_event.is_set():
                out["cancelled"] = True
                _safe_unlink(dest)
                return out
            out["path"] = dest
            log.info("registrazione copiata: %s (%d byte)",
                     dest, dest.stat().st_size)
            return out
        except Exception as exc:
            out["warnings"].append(f"copia registrazione fallita: {exc}")
            log.exception("copia registrazione fallita")
            return out

    ff = ffmpeg_path()
    if not ff:
        out["warnings"].append(
            "ffmpeg assente: conversione MP3 non disponibile — "
            "scegli un file .wav")
        return out
    total = wav_duration(temp_wav)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(temp_wav), "-codec:a", "libmp3lame", "-q:a", "4",
           "-progress", "pipe:1", "-nostats", str(dest)]

    def _pump() -> None:
        if on_progress is None or total <= 0:
            return
        for line in proc.stdout or ():
            # «out_time_ms» di -progress e' in MICROSECONDI (so scatter)
            m = line.strip().split("=")
            if len(m) == 2 and m[0] in ("out_time_us", "out_time_ms"):
                try:
                    t = int(m[1])
                except ValueError:
                    continue
                f = t / 1e6 if m[0] == "out_time_us" else t / 1e3 / 1e6
                try:
                    on_progress(min(1.0, max(0.0, f / total)))
                except Exception:               # noqa: BLE001
                    pass

    cancel_event = cancel_event or threading.Event()
    try:
        proc = subproc.popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    except Exception as exc:                    # noqa: BLE001
        out["warnings"].append(f"conversione MP3 non riuscita: {exc}")
        log.exception("conversione MP3 non riuscita")
        return out
    pumping = threading.Thread(target=_pump, daemon=True,
                               name="mp3-progress")
    pumping.start()
    while True:
        try:
            rc = proc.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel_event.is_set():
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:               # noqa: BLE001
                    pass
                out["cancelled"] = True
                _safe_unlink(dest)
                return out
    if rc == 0 and dest.is_file() and dest.stat().st_size > 0:
        if on_progress:
            try:
                on_progress(1.0)
            except Exception:                   # noqa: BLE001
                pass
        out["path"] = dest
        log.info("registrazione MP3 salvata: %s (%d byte)",
                 dest, dest.stat().st_size)
    else:
        out["warnings"].append(
            f"conversione MP3 non riuscita (ffmpeg exit {rc})")
        log.exception("conversione MP3 non riuscita")
        _safe_unlink(dest)
    return out


def _safe_unlink(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except Exception:                           # noqa: BLE001
        pass


def _copy_with_progress(src: Path, dest: Path, on_progress,
                        cancel_event: threading.Event | None) -> None:
    total = src.stat().st_size or 1
    copied = 0
    with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
        while True:
            chunk = fsrc.read(_COPY_CHUNK)
            if not chunk:
                break
            fdst.write(chunk)
            copied += len(chunk)
            if on_progress:
                try:
                    on_progress(min(1.0, copied / total))
                except Exception:               # noqa: BLE001
                    pass
    if on_progress:
        try:
            on_progress(1.0)
        except Exception:                       # noqa: BLE001
            pass


def cleanup_session(session_dir: Path) -> None:
    """Elimina i file temporanei della sessione (cartella inclusa)."""
    try:
        shutil.rmtree(session_dir, ignore_errors=True)
    except Exception:
        pass
