"""Provider di testo via GitHub Copilot (abbonamento dell'utente).

Usa lo stesso flusso di login di VS Code (device flow OAuth) e poi:

- scambio del token GitHub (ghu_) con un token Copilot breve tramite
  ``GET https://api.github.com/copilot_internal/v2/token`` (cache in
  memoria, si rinnova da solo 5 minuti prima della scadenza);
- chiamata ``POST https://api.githubcopilot.com/chat/completions``
  (OpenAI-compatibile) per riassunto, minute e traduzione.

Le credenziali stanno in ``%APPDATA%\\IACallMinute\\copilot.json``
(ghu_ + token Copilot corrente): NON in settings.json e mai nel log.
Le chiamate HTTP usano solo ``requests`` già presente nel progetto.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import requests

from .. import config
from .errors import ProviderError, net_error_text

log = logging.getLogger("iacm.copilot")

# Già noti (client OAuth dell'estensione Copilot di VS Code, hardcoded
# nell'estensione ufficiale): servono solo per il device flow di login.
_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_DEVICE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"
_API_URL = "https://api.githubcopilot.com/chat/completions"
_EDITOR_HEADERS = {
    "Editor-Version": "vscode/1.99.0",
    "Editor-Plugin-Version": "copilot-chat/0.27.0",
    "Copilot-Integration-Id": "vscode-chat",
    "User-Agent": f"IACallMinute/{config.APP_VERSION}",
}

CHATS_URL = "https://api.githubcopilot.com"

DEFAULT_MODEL = "gpt-4o-mini"

_CRED_FILE = config.APPDATA_DIR / "copilot.json"

_lock = threading.Lock()
_cache: dict = {          # token copilot in memoria
    "token": "",
    "expires_at": 0.0,
    "ghu": "",
}


def credentials_file() -> Path:
    return _CRED_FILE


def api_key(cfg: dict | None = None) -> str | None:
    """Token Copilot valido o None (senza lasciare file di log)."""
    return _ensure_token()


# --------------------------------------------------------------------------- #
# Credenziali su disco: ghu_ (lunga) + token Copilot corrente
# --------------------------------------------------------------------------- #
def _load() -> dict:
    try:
        if _CRED_FILE.is_file():
            data = json.loads(_CRED_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:                      # noqa: BLE001
        pass
    return {}


def _save(data: dict) -> None:
    try:
        _CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CRED_FILE.write_text(
            json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:                      # noqa: BLE001
        log.warning("salvataggio credenziali Copilot fallito", exc_info=True)


def is_logged_in() -> bool:
    """C'è un token GitHub (ghu_) salvato? (senza verificare la rete)."""
    return bool(_load().get("ghu"))


def logout() -> None:
    with _lock:
        _cache.update(token="", expires_at=0.0, ghu="")
    if _CRED_FILE.exists():
        try:
            _CRED_FILE.unlink()
        except OSError:
            pass
    log.info("credenziali GitHub Copilot rimosse")


# --------------------------------------------------------------------------- #
# Device flow (come VS Code): codice mostrato, l'utente apre /login/device
# --------------------------------------------------------------------------- #
def start_login() -> tuple[str, str]:
    """Chiede il device code a GitHub. Ritorna (codice, URL di verifica)."""
    r = requests.post(_DEVICE_URL, headers={"Accept": "application/json"},
                      data={"client_id": _CLIENT_ID, "scope": ""},
                      timeout=20)
    if not r.ok:
        raise ProviderError(
            f"GitHub non raggiungibile ({r.status_code})", r.status_code, True)
    d = r.json()
    return str(d["user_code"]), str(d.get("verification_uri", "https://github.com/login/device")) + (f"?user_code={d['user_code']}" if d.get("user_code") else "")


def finish_login(device_code: str, interval: float = 5.0) -> str:
    """Sonda fino al consenso dell'utente. Solleva ProviderError al fallimento."""
    deadline = time.monotonic() + 890          # come expires_in di GitHub
    while time.monotonic() < deadline:
        time.sleep(max(2.0, interval))
        r = requests.post(_TOKEN_URL, headers={"Accept": "application/json"},
                          data={"client_id": _CLIENT_ID,
                                "device_code": device_code,
                                "grant_type": "urn:ietf:params:oauth:"
                                              "grant-type:device_code"},
                          timeout=20)
        if r.ok:
            tok = r.json().get("access_token")
            if tok:
                with _lock:
                    _save({"ghu": tok})
                    _cache["ghu"] = tok
                    _cache["token"] = ""
                    _cache["expires_at"] = 0.0
                log.info("login GitHub Copilot riuscito")
                return tok
        else:
            log.info("device poll ignorato: HTTP %s", r.status_code)
    raise ProviderError("login Copilot non completato in tempo", 408, False)


# --------------------------------------------------------------------------- #
# Token Copilot (breve) con cache
# --------------------------------------------------------------------------- #
def _exchange(ghu: str) -> tuple[str, float]:
    headers = {"Accept": "application/json",
               "Authorization": f"token {ghu}", **_EDITOR_HEADERS}
    try:
        r = requests.get(_EXCHANGE_URL, headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise ProviderError(net_error_text(exc), 0, True) from exc
    if r.status_code == 401:
        # ghu_ scaduto: il device flow non dà un refresh token
        raise ProviderError(
            "sessione GitHub Copilot scaduta: riconnetti in Configurazione "
            "→ Testo → GitHub Copilot (Accedi)", 401, False)
    if r.status_code == 403:
        raise ProviderError(
            "GitHub non abilita il token: verifica che l'account abbia "
            "Copilot attivo", 403, False)
    if not r.ok:
        raise ProviderError(f"scambio token Copilot HTTP {r.status_code}",
                            r.status_code, True)
    try:
        data = r.json()
        ctok = str(data["token"]).strip()
        exp = float(data.get("expires_at") or 0) or (time.time() + 1500)
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError("risposta inattesa dallo scambio token", 200,
                            True) from exc
    if not ctok:
        raise ProviderError("scambio token: token vuoto", 200, True)
    return ctok, float(exp)


def _ensure_token() -> str:
    with _lock:
        ghu = _load().get("ghu") or _cache.get("ghu") or ""
        if _cache["token"] and time.time() < _cache["expires_at"] - 300:
            return _cache["token"]
        if not ghu:
            raise ProviderError(
                "GitHub Copilot non collegato: in Configurazione → Testo "
                "premi «🪪 Accedi con GitHub Copilot»", 401, False)
        ctok, exp = _exchange(ghu)
        _cache.update(token=ctok, expires_at=exp, ghu=ghu)
        _save({"ghu": ghu, "copilot_token": ctok, "expires_at": exp})
        return ctok


def chat_text(cfg: dict, system: str, user_text: str,
              timeout: float | None = None) -> str:
    """Riassunto/minute/traduzione via Copilot /chat/completions."""
    model = (cfg.get("model") or DEFAULT_MODEL).strip()
    tok = _ensure_token()
    headers = {"Authorization": f"Bearer {tok}",
               "Content-Type": "application/json",
               "Accept": "application/json", **_EDITOR_HEADERS}
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
    }
    try:
        resp = requests.post(_API_URL, headers=headers, json=payload,
                             timeout=timeout or config.GENERATE_TIMEOUT_S)
    except requests.RequestException as exc:
        raise ProviderError(net_error_text(exc), 0, True) from exc
    if resp.status_code == 400 and "not supported" in resp.text.lower() \
            and model != DEFAULT_MODEL:
        # modello non presente nell'abbonamento (o nome ripescato da un
        # altro provider): ritento una volta col default economico
        log.warning("modello %s non supportato: riprovo con %s",
                    model, DEFAULT_MODEL)
        payload["model"] = DEFAULT_MODEL
        try:
            resp = requests.post(_API_URL, headers=headers, json=payload,
                                 timeout=timeout or config.GENERATE_TIMEOUT_S)
        except requests.RequestException as exc:
            raise ProviderError(net_error_text(exc), 0, True) from exc
    if resp.status_code == 401:
        # token copilot scaduto a sorpresa: rinnovo e riprovo una volta
        with _lock:
            _cache.update(token="", expires_at=0.0)
        tok = _ensure_token()
        headers["Authorization"] = f"Bearer {tok}"
        try:
            resp = requests.post(_API_URL, headers=headers, json=payload,
                                 timeout=timeout or config.GENERATE_TIMEOUT_S)
        except requests.RequestException as exc:
            raise ProviderError(net_error_text(exc), 0, True) from exc
    if resp.status_code != 200:
        from .openai_compat import _raise_for
        _raise_for(resp)
    try:
        content = resp.json()["choices"][0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("risposta inattesa da Copilot: "
                            f"{resp.text[:200]}", 200, True) from exc
    if not str(content or "").strip():
        raise ProviderError("risposta vuota da Copilot", 200, True)
    log.info("chat ok: %d car. da GitHub Copilot (%s)", len(content), model)
    return str(content)


def list_models() -> list[str]:
    """Modelli chat disponibili sull'abbonamento (per il dialog errori)."""
    tok = _ensure_token()
    headers = {"Authorization": f"Bearer {tok}",
               "openai-intent": "model-access", "Accept": "application/json",
               **_EDITOR_HEADERS}
    try:
        r = requests.get(f"{CHATS_URL}/models", headers=headers, timeout=20)
        if not r.ok:
            return []
        out = []
        for m in r.json().get("data", []):
            mid = str(m.get("id") or "")
            cap = str(m.get("capabilities", {}).get("type", "")).lower()
            # solo i modelli di chat (esclui embedding/whisper)
            if mid and (not cap or "chat" in cap):
                out.append(mid)
        return sorted(set(out))
    except Exception:                      # noqa: BLE001
        return []
