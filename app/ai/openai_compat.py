"""Client REST per endpoint OpenAI-compatibili.

- STT:  POST {base}/audio/transcriptions  (Whisper-compat)
- Testo: POST {base}/chat/completions

Esempi di base_url:
  https://api.groq.com/openai/v1
  https://openrouter.ai/api/v1
  https://api.openai.com/v1
  http://localhost:1234/v1          (LM Studio, chiave non richiesta)
"""
from __future__ import annotations

import logging

import requests

from .. import config
from .errors import ProviderError, net_error_text

log = logging.getLogger("iacm.openai")


def _base(cfg: dict) -> str:
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise ProviderError(
            "URL endpoint mancante (es. https://api.groq.com/openai/v1)",
            0, False)
    return base


def _headers(cfg: dict) -> dict:
    headers: dict = {}
    key = (cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _model(cfg: dict) -> str:
    model = (cfg.get("model") or "").strip()
    if not model:
        raise ProviderError("modello non impostato", 0, False)
    return model


def _raise_for(resp: "requests.Response") -> None:
    try:
        body = resp.json()
    except ValueError:
        body = {"error": {"message": resp.text[:300]}}
    try:
        msg = body["error"]["message"]
    except Exception:
        msg = str(body)[:300]
    if resp.status_code == 429:
        retry_after = None
        raw = resp.headers.get("retry-after")
        try:
            retry_after = float(raw) if raw else None
        except ValueError:
            retry_after = None
        raise ProviderError(f"limite di richieste raggiunto: {msg}",
                            429, True, retry_after)
    if resp.status_code in (408, 500, 502, 503, 504):
        raise ProviderError(f"errore server endpoint: {msg}",
                            resp.status_code, True)
    raise ProviderError(f"HTTP {resp.status_code}: {msg}",
                        resp.status_code, False)


def chat_text(cfg: dict, system: str, user_text: str,
              timeout: float | None = None) -> str:
    """Chiamata di testo generica su /chat/completions."""
    url = f"{_base(cfg)}/chat/completions"
    payload = {
        "model": _model(cfg),
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
    }
    try:
        resp = requests.post(url, headers=_headers(cfg), json=payload,
                             timeout=timeout or config.GENERATE_TIMEOUT_S)
    except requests.RequestException as exc:
        raise ProviderError(net_error_text(exc), 0, True) from exc
    if resp.status_code != 200:
        _raise_for(resp)
    data = None
    try:
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderError(
            f"risposta inattesa dal router: "
            f"{str(data) if data is not None else resp.text[:200]}",
            200, True) from exc
    if not str(content or "").strip():
        # modelli "reasoning" (es. gpt-oss su Groq): quando il canale
        # «content» resta vuoto il testo finisce nel campo «reasoning».
        # Meglio restituire quello che fallire con "risposta vuota".
        reasoning = ""
        try:
            reasoning = str(data["choices"][0]["message"].get("reasoning") or "")
        except (KeyError, IndexError, TypeError, AttributeError):
            pass
        if reasoning.strip():
            log.warning("content vuoto: uso il campo «reasoning» (%d car.)",
                        len(reasoning))
            return reasoning.strip()
        raise ProviderError("risposta vuota dal modello", 200, True)
    log.info("chat ok: %d car. da %s", len(content), cfg.get("base_url"))
    return str(content)


def transcribe_audio(cfg: dict, wav_bytes: bytes,
                     filename: str = "segmento.wav",
                     language: str | None = None) -> str:
    """Trascrizione via /audio/transcriptions (formato: testo grezzo)."""
    url = f"{_base(cfg)}/audio/transcriptions"
    files = {"file": (filename, wav_bytes, "audio/wav")}
    data = {"model": _model(cfg), "response_format": "json"}
    if language and language != "auto":
        data["language"] = language     # ISO-639-1: evita misdetection
    try:
        resp = requests.post(url, headers=_headers(cfg), files=files,
                             data=data, timeout=config.REQUEST_TIMEOUT_S)
    except requests.RequestException as exc:
        raise ProviderError(net_error_text(exc), 0, True) from exc
    if resp.status_code != 200:
        _raise_for(resp)
    try:
        body = resp.json()
        text = str(body.get("text") or "")
    except ValueError:
        text = resp.text
    log.info("trascrizione ok: %d car. da %s", len(text), cfg.get("base_url"))
    return text
