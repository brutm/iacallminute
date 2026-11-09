"""Facciata AI unificata: sceglie il provider (Gemini / OpenAI-compat)
dai settings per trascrizione, traduzione e testo generativo.

Tutto il resto dell'app (pipeline, UI, selftest) parla solo con questo modulo.
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import struct
import time
import threading

from .. import config
from . import copilot, gemini, openai_compat, prompts
from .errors import ProviderError

log = logging.getLogger("iacm.ai")

# Cache per available_models con TTL (5 minuti)
_models_cache: dict[str, tuple[float, list[str]]] = {}
_models_cache_lock = threading.Lock()
_MODELS_TTL_S = 300.0


def _call(model: str, do_call):
    """Esegue una singola chiamata al modello scelto (nessun fallback).

    Un errore viene propagato così com'è: tocca all'interfaccia avvisare
    l'utente e decidere se riprovare con lo stesso modello o cambiarlo.
    """
    log.info("chiamata modello %s", model)
    t0 = time.monotonic()
    try:
        res = do_call(model)
    except Exception as exc:                           # noqa: BLE001
        log.warning("modello %s non disponibile (%.1fs): %s",
                    model, time.monotonic() - t0, exc)
        raise
    log.info("risposta da modello %s in %.1fs", model, time.monotonic() - t0)
    return res


def available_models(cfg: dict | None = None) -> list[str]:
    """Elenco dei modelli Gemini utilizzabili (per il dialog di errore).

    Cache in memoria con TTL di 5 minuti per evitare richieste HTTP ripetute.
    """
    key = config.stt_key(cfg or config.SETTINGS.get("stt"))
    cache_key = key or "no-key"
    now = time.monotonic()
    with _models_cache_lock:
        cached = _models_cache.get(cache_key)
        if cached and (now - cached[0]) < _MODELS_TTL_S:
            return cached[1]
    # Cache miss o scaduta: facciamo la richiesta
    out: list[str] = []
    if key:
        try:
            import requests
            resp = requests.get(
                f"{config.API_BASE}/models",
                params={"key": key, "pageSize": 200},
                timeout=10)
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    name = str(m.get("name", "")).replace("models/", "")
                    methods = m.get("supportedGenerationMethods") or []
                    if name and "generateContent" in methods:
                        out.append(name)
        except Exception as exc:                       # noqa: BLE001
            log.info("elenco modelli non disponibile: %s", exc)
    if not out:
        result = list(config.SUGGESTED_MODELS)
    else:
        bad = ("image", "tts", "banana", "robotics", "computer-use", "omni")
        gemini_only = [m for m in out
                       if "gemini" in m and not any(b in m for b in bad)]
        if not gemini_only:
            result = list(config.SUGGESTED_MODELS)
        else:
            head = [m for m in config.SUGGESTED_MODELS if m in gemini_only]
            tail = [m for m in sorted(gemini_only) if m not in head]
            result = head + tail
    with _models_cache_lock:
        _models_cache[cache_key] = (now, result)
    return result


# --------------------------------------------------------------------------- #
# Testo (riassunto / minute / traduzione)
# --------------------------------------------------------------------------- #
def complete(settings: dict, system: str, user_text: str,
             on_status=None) -> str:
    """Genera un testo (riassunto/minute) con il provider scelto.

    Gli errori TRANSITORI (connessione interrotta, timeout, 5xx) vengono
    ripetuti con attesa crescente: prima la generazione non ritentava e una
    singola connessione caduta («Connection aborted» 10054) faceva perdere
    tutto il lavoro. Con on_status si aggiorna la UI durante i tentativi.
    """
    cfg = settings["text"]
    typ = cfg.get("type", "gemini")
    model = (cfg.get("model") or config.DEFAULT_MODEL).strip()
    log.info("testo %s: model=%s (system=%d, user=%d car.)",
             typ, model, len(system), len(user_text))
    if typ == "gemini":
        key = config.text_key(cfg, settings.get("stt"))
        if not key:
            raise ProviderError(
                "chiave API Gemini mancante (Configurazione → Testo)",
                401, False)
        call = lambda m: gemini.complete_text(  # noqa: E731
            key, m, system, user_text, timeout=config.GENERATE_TIMEOUT_S)
    elif typ == "copilot":
        call = lambda m: copilot.chat_text(     # noqa: E731
            cfg, system, user_text, timeout=config.GENERATE_TIMEOUT_S)
    else:
        log.info("testo openai: base=%s model=%s (user=%d car)",
                 cfg.get("base_url"), cfg.get("model"), len(user_text))
        call = lambda m: openai_compat.chat_text(  # noqa: E731
            cfg, system, user_text, timeout=config.GENERATE_TIMEOUT_S)

    last_exc: Exception | None = None
    for attempt in range(1, max(1, int(config.MAX_RETRIES)) + 1):
        try:
            if typ == "gemini":
                return _call(model, call)
            return call(model)
        except ProviderError as exc:
            last_exc = exc
            retryable = getattr(exc, "retryable", False)
            log.warning("generazione fallita (tentativo %d): %s "
                        "(retryable=%s)", attempt, exc, retryable)
            if not retryable or attempt >= config.MAX_RETRIES:
                raise
            delay = min(config.BACKOFF_MAX_S,
                        config.BACKOFF_BASE_S * (2 ** (attempt - 1)))
            msg = (f"connessione interrotta ({exc}) — riprovo tra "
                   f"{delay:.0f}s (tentativo {attempt + 1}/"
                   f"{config.MAX_RETRIES})")
            log.info(msg)
            if on_status is not None:
                try:
                    on_status(msg)
                except Exception:                  # noqa: BLE001
                    pass
            time.sleep(delay)
    raise last_exc if last_exc else ProviderError(
        "generazione non riuscita", 0, False)


# --------------------------------------------------------------------------- #
# Blocco lingua di sessione (consenso automatico)
# --------------------------------------------------------------------------- #
# Whisper in «Rilevamento automatico» sbaglia lingua sui segmenti corti
# (es. inglese letto come olandese). Quando _LANG_CONSENSUS segmenti
# consecutivi concordano sulla stessa lingua, questa viene bloccata per
# tutta la sessione: i segmenti seguenti vengono trascritti forzandola,
# e il consenso non viene più ricalcolato.
_LANG_LK = threading.Lock()
_lang_recent: list[str] = []
_lang_locked: str | None = None
_LANG_CONSENSUS = 3          # segmenti consecutivi uguali per bloccare
_LANG_WINDOW = 6             # memoria dei rilevamenti (finestra scorrevole)


def reset_session_language() -> None:
    """Nuova sessione: il rilevamento automatico riparte da zero (A3)."""
    global _lang_locked
    with _LANG_LK:
        _lang_recent.clear()
        _lang_locked = None


def _session_lang(stt: dict, detected: str | None = None) -> str:
    """Lingua da usare per il segmento corrente.

    Senza `detected` ritorna la lingua da applicare (consenso già
    bloccato, altrimenti «auto»). Con `detected` aggiorna la memoria:
    quando _LANG_CONSENSUS rilevamenti consecutivi coincidono, la lingua
    viene bloccata. Una lingua forzata manualmente in Configurazione
    vince sempre e il consenso non viene aggiornato.
    """
    global _lang_locked
    manual = ((stt.get("language") or "auto").strip().lower() or "auto")
    if manual != "auto":
        return manual
    with _LANG_LK:
        if _lang_locked:
            return _lang_locked
        if detected and detected not in ("", "auto", "unknown"):
            code = detected.strip().lower()[:2]
            if code.isalpha() and code.isascii():
                _lang_recent.append(code)
                del _lang_recent[:-_LANG_WINDOW]
                tail = _lang_recent[-_LANG_CONSENSUS:]
                if (len(_lang_recent) >= _LANG_CONSENSUS
                        and len(set(tail)) == 1):
                    _lang_locked = code
                    log.info("lingua di sessione bloccata: %s "
                             "(consenso su %d segmenti)", code,
                             _LANG_CONSENSUS)
                    return code
    return "auto"


def transcribe_segment(settings: dict, wav_bytes: bytes, *,
                       duration: float, start: float,
                       bilingual: bool) -> dict:
    """Trascrive un segmento WAV con il provider scelto in settings.

    Restituisce {"language", "italian", "original"}.
    """
    stt = settings["stt"]
    typ = stt.get("type", "gemini")
    lang = _session_lang(stt)

    if typ == "offline":
        from . import offline
        res = offline.transcribe(stt, wav_bytes, duration=duration,
                                 start=start, bilingual=bilingual,
                                 language=lang)
        _session_lang(stt, res.get("language"))
        return res

    if typ == "gemini":
        key = config.stt_key(stt)
        if not key:
            raise ProviderError(
                "chiave API Gemini mancante (Configurazione → Trascrizione)",
                401, False)
        model = (stt.get("model") or config.DEFAULT_MODEL).strip()
        log.info("stt gemini: model=%s dur=%.1fs t=%.1fs bilingual=%s "
                 "lang=%s", model, duration, start, bilingual, lang)
        res = _call(model, lambda m: gemini.transcribe_segment(
            key, m, wav_bytes, duration=duration, start=start,
            bilingual=bilingual, lang_hint=lang))
        _session_lang(stt, res.get("language"))
        return res

    # --- endpoint OpenAI-compat: whisper grezzo + traduzione via testo ---
    log.info("stt openai: base=%s model=%s dur=%.1fs t=%.1fs lang=%s",
             stt.get("base_url"), stt.get("model"), duration, start, lang)
    original = (openai_compat.transcribe_audio(
        stt, wav_bytes,
        language=None if lang == "auto" else lang) or "").strip()
    if not original:
        return {"language": "", "italian": "", "original": ""}
    parsed = _translate_json(settings, original)
    _session_lang(stt, parsed.get("language"))
    italian = parsed.get("italian", "").strip()
    if not bilingual:
        return {"language": parsed.get("language", "it"),
                "italian": italian, "original": ""}
    return {"language": parsed.get("language", "it"),
            "italian": italian, "original": original}


def analyze_video(settings: dict, video, frames_dir) -> str:
    """Analisi visiva: estrae frame del video e li descrive con l'AI (Gemini).

    Dettaglia il riassunto: codice mostrato, schermate, piattaforma della
    call e, quando l'anteprima è evidenziata, chi sta parlando.
    """
    from ..storage import screenrec

    frames = screenrec.extract_frames(video, frames_dir, count=12)
    if not frames:
        raise ProviderError("nessun frame estratto dal video", 200, False)
    try:
        dur = screenrec.video_duration(video)
    except Exception:
        dur = float(len(frames)) * 4.0

    cfg = settings["text"]
    typ = cfg.get("type", "gemini")
    if typ != "gemini":
        raise ProviderError(
            "analisi video disponibile solo con provider Gemini "
            "(Configurazione -> Testo)", 400, False)
    key = config.text_key(cfg, settings.get("stt"))
    if not key:
        raise ProviderError("chiave API Gemini mancante", 401, False)
    model = (cfg.get("model") or config.DEFAULT_MODEL).strip()
    stamps = ", ".join(f"frame {i} -> {t}"
                       for i, (t, _) in enumerate(frames, 1))
    from . import prompts
    user = prompts.ANALISI_VIDEO_USER.format(
        duration=f"{dur:.0f}", count=len(frames), stamps=stamps)
    images = [(t, path.read_bytes()) for t, path in frames]
    log.info("analisi video: %d frame, durata %.0fs, modello=%s",
             len(images), dur, model)
    return _call(model, lambda m: gemini.analyze_images(
        key, m, prompts.ANALISI_VIDEO_SYSTEM, images, user))


def _translate_json(settings: dict, text: str) -> dict:
    try:
        raw = complete(settings, prompts.TRANSLATE_SYSTEM, text)
        parsed = _parse_json(raw)
        return {
            "language": str(parsed.get("language") or "").strip() or "it",
            "italian": str(parsed.get("italian") or "").strip(),
        }
    except Exception as exc:
        log.warning("traduzione via testo fallita: %s; uso fallback", exc)
        return {"language": "it", "italian": text}


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ProviderError(f"JSON non valido dal modello: {text[:200]}",
                                200, True)
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"JSON non valido dal modello: {text[:200]}",
                                200, True) from exc
    if not isinstance(parsed, dict):
        raise ProviderError("formato risposta inatteso", 200, True)
    return parsed


# --------------------------------------------------------------------------- #
# Test di connessione (finestra Configurazione)
# --------------------------------------------------------------------------- #
def _tone_wav(seconds: float = 0.8, freq: float = 440.0) -> bytes:
    """Tono WAV 16 kHz mono int16 (in memoria) per test STT."""
    rate = config.TARGET_RATE
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        val = int(12000 * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", val)
    buf = io.BytesIO()
    import wave
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def test_settings(settings: dict) -> str:
    """Verifica credenziali/connessione dei provider scelti.

    Solleva ProviderError in caso di errore; ritorna un riepilogo.
    """
    details: list[str] = []

    # testo
    answer = complete(settings, "Rispondi in italiano con la sola parola: ok.",
                      "Test di connessione IACallMinute.")
    details.append(f"testo ok: «{answer.strip()[:40]}»")

    # trascrizione
    stt = settings["stt"]
    if stt.get("type") == "offline":
        try:
            import faster_whisper                  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "pacchetto 'faster-whisper' non installato "
                "(pip install faster-whisper)", 0, False) from exc
        name = ((stt.get("model") or "").strip()
                or config.DEFAULT_OFFLINE_MODEL)
        details.append(f"trascrizione offline ok (whisper {name}; al primo "
                       "uso il modello viene scaricato, poi senza rete)")
        return " · ".join(details)
    if stt.get("type") == "gemini":
        key = config.stt_key(stt)
        if not key:
            raise ProviderError("chiave API Gemini mancante per la trascrizione",
                                401, False)
        model = (stt.get("model") or config.DEFAULT_MODEL).strip()
        gemini.transcribe_segment(key, model, _tone_wav(),
                                  duration=0.8, start=0.0, bilingual=False)
        details.append(f"trascrizione gemini ok (model={model})")
    else:
        text = openai_compat.transcribe_audio(stt, _tone_wav())
        details.append(
            f"trascrizione endpoint ok (model={stt.get('model')}, "
            f"{len(text or '')} car.)")
    return " · ".join(details)
