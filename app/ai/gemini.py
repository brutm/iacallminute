"""Client REST per Google Gemini (generativelanguage v1beta)."""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import requests

from .. import config
from .errors import ProviderError as GeminiError  # alias retro-compatibile
from .errors import net_error_text


def _error_message(body: Any) -> str:
    try:
        return body["error"]["message"]
    except Exception:
        return str(body)[:300]


def _retry_after_from_body(body: Any) -> float | None:
    try:
        for detail in body["error"].get("details", []):
            if detail.get("@type", "").endswith("RetryInfo"):
                delay = detail.get("retryDelay", "")
                m = re.match(r"([\d.]+)s", delay)
                if m:
                    return float(m.group(1))
    except Exception:
        pass
    return None


def _post(api_key: str, model: str, payload: dict,
          timeout: float | None = None) -> dict:
    url = f"{config.API_BASE}/models/{model}:generateContent"
    try:
        resp = requests.post(
            url, params={"key": api_key}, json=payload,
            timeout=timeout or config.REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        # "Connection aborted"/10054 e gli altri reset: la chiamata si puo'
        # ripetere senza perdere nulla
        raise GeminiError(net_error_text(exc), status=0, retryable=True) from exc

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as exc:
            raise GeminiError("risposta non JSON dal server", 200, retryable=True) from exc

    try:
        body = resp.json()
    except ValueError:
        body = {"error": {"message": resp.text[:300]}}

    msg = _error_message(body)
    if resp.status_code == 429:
        out = f"quota raggiunta: {msg}"
        if "retry in" in msg or "Quota exceeded" in msg:
            out += ("\n\nNota: e' la quota GRATUITA giornaliera del "
                    "modello (contatore diverso da RPM/TPM mostrati nel "
                    "dashboard). Riprova piu' tardi, oppure in "
                    "Configurazione scegli un altro modello o una chiave "
                    "con piano a pagamento.")
        raise GeminiError(out, 429, True,
                          _retry_after_from_body(body))
    if resp.status_code in (500, 502, 503, 504):
        raise GeminiError(f"errore server Google: {msg}", resp.status_code, True)
    raise GeminiError(f"HTTP {resp.status_code}: {msg}", resp.status_code, False)


def _extract_text(data: dict) -> str:
    feedback = data.get("promptFeedback") or {}
    block = feedback.get("blockReason")
    if block:
        raise GeminiError(f"richiesta bloccata dal modello: {block}", 200, False)
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"risposta senza contenuto: {str(data)[:200]}", 200,
                          retryable=True) from exc
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise GeminiError("risposta vuota dal modello", 200, retryable=True)
    return text


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def build_wav_bytes(pcm: "bytes", sample_rate: int = config.TARGET_RATE) -> bytes:
    """Wrappa PCM int16 mono in un contenitore WAV."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def transcribe_segment(api_key: str, model: str, wav_bytes: bytes, *,
                       duration: float, start: float, bilingual: bool,
                       lang_hint: str = "auto") -> dict:
    """Trascrive (e traduce se bilingual) un segmento WAV.

    Restituisce: {"language": str, "original"?: str, "italian": str}
    ``lang_hint``: "auto" oppure lingua forzata (es. "it"): con la lingua
    imposta il modello non confonde l'italiano con lo spagnolo.
    """
    from ..ai import prompts

    system = prompts.TRANSCRIBE_SYSTEM_BILINGUAL if bilingual else prompts.TRANSCRIBE_SYSTEM_IT
    if lang_hint and lang_hint != "auto":
        system += (f" La lingua del parlante è {lang_hint.upper()}: trascrivi "
                   f"e riporta «language»: \"{lang_hint}\" anche se il "
                   f"rilevamento automatico direbbe altro.")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "audio/wav",
                                 "data": base64.b64encode(wav_bytes).decode("ascii")}},
                {"text": prompts.TRANSCRIBE_USER.format(duration=duration, start=start)},
            ],
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }
    data = _post(api_key, model, payload)
    raw = _strip_fences(_extract_text(data))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"JSON non valido dal modello: {raw[:200]}", 200,
                          retryable=True) from exc
    if not isinstance(parsed, dict):
        raise GeminiError("formato trascrizione inatteso", 200, retryable=True)

    italian = str(parsed.get("italian") or "").strip()
    language = str(parsed.get("language") or "").strip() or "it"
    original = str(parsed.get("original") or "").strip()
    if original and language.lower().startswith("it"):
        # Audio in italiano: nessuna traduzione verso altre lingue. Alcune
        # risposte del modello mettono nel campo «italian» una traduzione
        # in inglese (o simile): qui viene riportata all'originale.
        italian = original
    return {"language": language, "italian": italian, "original": original}


def analyze_images(api_key: str, model: str, system: str,
                   frames: list, user_text: str) -> str:
    """Analisi multi-frame (JPG + timestamp) -> testo Markdown in italiano.

    ``frames``: lista di (stampa_mmss, bytes_jpeg).
    """
    parts: list[dict] = []
    for i, (stamp, jpg) in enumerate(frames, 1):
        parts.append({"text": f"frame {i} ({stamp}):"})
        parts.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(jpg).decode("ascii")}})
    parts.append({"text": user_text})
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
        },
    }
    data = _post(api_key, model, payload)
    return _strip_fences(_extract_text(data))


def complete_text(api_key: str, model: str, system: str, user_text: str,
                  timeout: float | None = None) -> str:
    """Chiamata di testo generica (riassunto / minute)."""
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    data = _post(api_key, model, payload,
                 timeout=timeout or config.GENERATE_TIMEOUT_S)
    return _strip_fences(_extract_text(data))
