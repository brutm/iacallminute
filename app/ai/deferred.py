"""Analisi differita di un file audio/video dal disco.

Serve per fare riassunto/minute piu' avanti partendo dal file salvato
(MP4 con o senza audio, WAV, MP3…): l'audio viene convertito in WAV,
trascritto a blocchi (~3 min, per stare nei limiti delle API) e poi gli
stessi prompt delle sessioni live generano il documento. Con un video si
puo' chiedere anche l'analisi visiva dei frame.
"""
from __future__ import annotations

import array
import io
import logging
import re
import threading
import wave
from pathlib import Path

from .. import config, subproc
from ..storage import outputs, recorder
from . import service
from .errors import ProviderError

log = logging.getLogger("iacm.ai.deferred")

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
CHUNK_S = 180            # blocchi di trascrizione (~180 s = ~11 MB WAV/16k)


def is_supported(path: Path) -> bool:
    return Path(path).suffix.lower() in (VIDEO_EXTS | AUDIO_EXTS)


def is_video(path: Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def _wrap_wav(pcm: bytes, rate: int, ch: int, sw: int) -> bytes:
    """Wrappa PCM grezzo in un contenitore WAV (in memoria)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _downmix(stereo: bytes, ch: int) -> bytes:
    """Media dei canali int16 verso mono (senza dipendenze extra)."""
    arr = array.array("h", stereo)
    n = len(arr) // ch
    out = array.array("h", bytes(2 * n))
    for i in range(n):
        s = 0
        for k in range(ch):
            s += arr[i * ch + k]
        out[i] = max(-32768, min(32767, s // ch))
    return out.tobytes()


def to_wav(path: Path, out_dir: Path, on_status=None) -> tuple[Path, float]:
    """Porta il sorgente a WAV (mono int16) e ritorna (percorso, durata s).

    ffmpeg serve per qualunque formato non-WAV (MP3, MP4, M4A…); un .wav
    gia' mono/int16 si legge direttamente senza ffmpeg.
    """
    src = Path(path)
    out = Path(out_dir) / "deferred.wav"
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    if src.suffix.lower() == ".wav":
        try:
            with wave.open(str(src), "rb") as w:
                rate, ch, sw = (w.getframerate(), w.getnchannels(),
                                w.getsampwidth())
                frames = w.readframes(w.getnframes())
        except wave.Error as exc:
            raise ProviderError(
                f"WAV non leggibile ({exc}): prova ffmpeg su PATH per "
                "ricodificarlo", 0, False) from exc
        if sw != 2 or rate < 8000:
            raise ProviderError(
                f"WAV {sw * 8} bit a {rate} Hz non supportato: ricodifica "
                "con ffmpeg (o converti 16 kHz int16)", 0, False)
        if ch == 1:
            dur = len(frames) / (2.0 * rate)
            return src, dur
        if on_status:
            on_status("conversione stereo → mono in corso…")
        mono = _downmix(frames, ch)
        out.write_bytes(_wrap_wav(mono, rate, 1, 2))
        return out, len(mono) / (2.0 * rate)

    ff = recorder.ffmpeg_path()
    if not ff:
        raise ProviderError(
            "ffmpeg non trovato nel PATH: serve per estrarre l'audio da "
            f"{src.suffix} (in alternativa carica un file .wav)", 0, False)
    if on_status:
        on_status(f"conversione «{src.name}» in WAV in corso…")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-vn", "-ac", "1", "-ar", str(config.TARGET_RATE),
           "-sample_fmt", "s16", str(out)]
    r = subproc.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
        raise ProviderError(
            f"conversione audio non riuscita: {(r.stderr or '')[-300:]}",
            0, False)
    with wave.open(str(out), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
    log.info("sorgente convertito: %s -> %s (%.1fs)", src, out, dur)
    return out, dur


def chunk_wav(path: Path, chunk_s: float = CHUNK_S
              ) -> list[tuple[float, bytes]]:
    """Divide il WAV in blocchi; ritorna [(start_s, wav_bytes), ...]."""
    out: list[tuple[float, bytes]] = []
    with wave.open(str(path), "rb") as w:
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        nframes = w.getnframes()
        step = max(1, int(chunk_s * rate))
        for start in range(0, nframes, step):
            pcm = w.readframes(min(step, nframes - start))
            out.append((start / rate, _wrap_wav(pcm, rate, ch, sw)))
    return out


def transcribe_file(settings: dict, path: Path, tmp_dir: Path, *,
                    visual: bool = False, chunk_s: float = CHUNK_S,
                    on_progress=None,
                    cancel: threading.Event | None = None) -> dict:
    """Trascrive il file a blocchi; per i video puo' aggiungere l'analisi
    visiva dei frame.

    Ritorna {"transcript", "visual", "duration", "chunks"}.
    """
    cancel = cancel or threading.Event()
    service.reset_session_language()

    def rep(frac: float, msg: str) -> None:
        if on_progress:
            on_progress(frac, msg)

    rep(0.02, "preparazione del file…")
    wav, dur = to_wav(path, Path(tmp_dir), on_status=rep)
    chunks = chunk_wav(wav, chunk_s=chunk_s)
    if not chunks:
        raise ProviderError(f"nessun audio dentro «{Path(path).name}»",
                            0, False)
    lines: list[str] = []
    total = len(chunks)
    for i, (start, wav_bytes) in enumerate(chunks, 1):
        if cancel.is_set():
            raise ProviderError("analisi annullata dall'utente", 0, False)
        rep(min(0.65, 0.05 + 0.6 * i / total),
            f"trascrizione blocco {i}/{total}…")
        r = service.transcribe_segment(settings, wav_bytes,
                                       duration=dur / total, start=start,
                                       bilingual=False)
        txt = ((r.get("italian") or "") or (r.get("original") or "")).strip()
        if txt:
            lines.append(f"[{outputs.format_ts(start)}] {txt}")
        log.info("blocco differito %d/%d (%d car.)", i, total, len(txt))
    if not lines:
        raise ProviderError(
            "nessun parlato riconosciuto: controlla il provider di "
            "trascrizione (Configurazione → Trascrizione) e la lingua "
            "selezionata", 0, False)
    visual_text = ""
    if visual and is_video(path):
        if cancel.is_set():
            raise ProviderError("analisi annullata dall'utente", 0, False)
        rep(0.7, "estrazione frame e analisi visiva del video…")
        visual_text = service.analyze_video(settings, Path(path),
                                            Path(tmp_dir) / "frames")
    rep(0.8, "trascrizione completata")
    log.info("trascrizione differita ok: %d blocchi, %.1fs, %d car.",
             total, dur, sum(len(x) for x in lines))
    return {"transcript": "\n".join(lines), "visual": visual_text,
            "duration": dur, "chunks": total}


def generate_document(settings: dict, kind: str, data: dict,
                      src_path: Path, *,
                      context_text: str = "",
                      on_status=None) -> str:
    """Genera riassunto o minute dalla trascrizione differita.

    Usa gli stessi prompt delle sessioni live: struttura e dettaglio
    restano identici.
    """
    from . import prompts
    origine = (f"NOTA: trascrizione ricostruita dall'analisi DIFFERITA del "
               f"file «{Path(src_path).name}» (durata: "
               f"{outputs.format_ts(data['duration'])}; i timestamp sono "
               f"dall'inizio del file).")
    visual = (f"\n\n{data['visual']}" if data.get("visual") else
              "(nessuna analisi video per questo file)")
    context = context_text or "(nessuno)"
    if kind == "minutes":
        system = prompts.MINUTES_SYSTEM
        user = prompts.MINUTES_USER.format(
            duration=outputs.format_ts(data["duration"]),
            transcript=origine + "\n\n" + data["transcript"],
            date=outputs.display_date(), context=context, visual=visual)
    else:
        system = prompts.SUMMARY_SYSTEM
        user = prompts.SUMMARY_USER.format(
            duration=outputs.format_ts(data["duration"]),
            transcript=origine + "\n\n" + data["transcript"],
            date=outputs.display_date(), context=context, visual=visual)
    if on_status:
        on_status("generazione del documento con l'IA (puo' volerci un "
                  "minuto)…")
    return service.complete(settings, system, user, on_status=on_status)


def source_stamp(path: Path) -> str:
    """Timbro per il nome del documento: la data dentro il nome del file
    di registrazione dell'app (…_20260101_120000.mp4) se c'e', altrimenti
    la data di oggi."""
    m = re.search(r"(\d{8}_\d{6})", Path(path).stem)
    if m:
        return m.group(1)
    return outputs.stamp_now()
