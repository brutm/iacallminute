r"""Configurazione globale di IACallMinute.

- Costanti audio/VAD/API
- Settings persistenti in %APPDATA%\IACallMinute\settings.json
- Log su file in %APPDATA%\IACallMinute\logs\app.log (disattivato di
  default: con il log spento non viene creato nessun file
- Pulizia delle cartelle temporanee (session_*)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import time
from pathlib import Path

APP_NAME = "IACallMinute"
APP_VERSION = "1.1.2"

TARGET_RATE = 16000  # Hz, mono, int16: formato unico di tutto il pipeline

# --- Modello AI -------------------------------------------------------------
DEFAULT_MODEL = os.environ.get("IACM_MODEL", "gemini-3.5-flash-lite")
# Trascrizione offline (faster-whisper): modelli ammessi e default.
# small = buona accuratezza e abbastanza veloce per la trascrizione live;
# medium e' ~3x piu' lento su CPU (uso solo su sessioni non tempo-reale).
OFFLINE_SIZES = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_OFFLINE_MODEL = os.environ.get("IACM_OFFLINE_MODEL", "small")
# Nessun fallback automatico: se il modello scelto fallisce si avvisa
# l'utente e si sceglie insieme come proseguire (vedi Pipeline/ FaultDialog).
# Modelli proposti nel dialog di errore quando l'elenco non e' raggiungibile.
SUGGESTED_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]
API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# --- Cartelle applicazione --------------------------------------------------
APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
LOG_DIR = APPDATA_DIR / "logs"
SETTINGS_FILE = APPDATA_DIR / "settings.json"

# Lingue selezionabili in Configurazione (trascrizione): "auto" usa il
# rilevamento automatico del provider; con una lingua forzata Whisper (e
# Gemini) non puo' confondere l'italiano con lo spagnolo.
STT_LANGS: list[tuple[str, str]] = [
    ("auto", "Rilevamento automatico"),
    ("it", "Italiano (forzato)"),
    ("en", "Inglese"),
    ("es", "Spagnolo"),
    ("fr", "Francese"),
    ("de", "Tedesco"),
    ("pt", "Portoghese"),
]

# Dispositivo di calcolo per la trascrizione offline (Whisper):
# "auto" usa la GPU quando c'e' (con le DLL in modelli/cuda_dlls) e cade
# su CPU in caso contrario; "gpu" la forza; "cpu" la disattiva.
STT_DEVICES: list[tuple[str, str]] = [
    ("auto", "Automatico (GPU se disponibile)"),
    ("gpu", "GPU (forzata)"),
    ("cpu", "CPU (forzata)"),
]

DEFAULT_SETTINGS: dict = {
    "temp_root": "C:/Temp/IACallMinute",
    "output_dir": "C:/Temp/IACallMinute",
    "bilingual": True,
    "log_enabled": False,
    "record_video": False,      # True: sessione con video MP4 + audio
    "screen_area": {"mode": "screen", "rect": None, "fps": 15},
    # Arresto automatico: si memorizzano solo i valori (minuti / orario);
    # la modalità attiva vale per la sessione e riparte «Disattivato».
    # save_*: documenti salvati da soli allo scadere del timer (oltre
    # all'audio/video); riassunto e minute richiedono il provider di testo.
    "auto_stop": {"minutes": 60, "at": "18:00", "save_transcript": True,
                  "save_summary": False, "save_minutes": False},
    "stt": {
        "type": "gemini",          # gemini | openai
        "api_key": "",
        "model": DEFAULT_MODEL,
        "base_url": "",
        "language": "auto",        # auto | it | en | es | fr | de | pt
        "device": "auto",          # auto | gpu | cpu (solo offline)
    },
    "text": {
        # Groq free tier (https://console.groq.com/keys): 1000 richieste/giorno,
        # nessun addestramento sui dati, API compatibile OpenAI.
        "type": "openai",        # gemini | openai
        "api_key": "",
        "model": "openai/gpt-oss-20b",
        "base_url": "https://api.groq.com/openai/v1",
    },
}

# Preset pronti per il provider di testo (riassunto/minute/traduzione):
# un click in Configurazione riempie tipo, URL e modello.
TEXT_PRESETS: dict[str, dict] = {
    "groq": {
        "label": "Groq (gratuito)",
        "type": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-20b",
        "hint": "chiave gratis su console.groq.com/keys · 1000 richieste/giorno",
    },
    "openrouter": {
        "label": "OpenRouter (gratuito)",
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/free",
        "hint": "chiave gratis su openrouter.ai/keys · modelli :free, 50 req/giorno",
    },
    "mistral": {
        "label": "Mistral (gratuito)",
        "type": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "hint": "10 $/mese di credito gratis su console.mistral.ai",
    },
    "gemini": {
        "label": "Gemini",
        "type": "gemini",
        "base_url": "",
        "model": DEFAULT_MODEL,
        "hint": "chiave gratis su aistudio.google.com/apikey",
    },
    "copilot": {
        "label": "GitHub Copilot",
        "type": "copilot",
        "base_url": "https://api.githubcopilot.com",
        "model": "gpt-4o-mini",
        "hint": "usa l'abbonamento GitHub Copilot: niente chiave, "
                "premi «🪪 Accedi» (più economico: gpt-4o-mini)",
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, val in (extra or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_settings() -> dict:
    try:
        if SETTINGS_FILE.is_file():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # il contesto vale solo per la sessione corrente:
                # non viene mai letto da (né scritto su) settings.json
                data.pop("context_notes", None)
                data.pop("context_folder", None)
                # la casella «Con video» non si memorizza piu': di default
                # la sessione e' senza video (C:/Temp si riempiva di MP4)
                data.pop("record_video", None)
                return _deep_merge(DEFAULT_SETTINGS, data)
    except Exception:
        logging.getLogger("iacm.config").warning(
            "settings.json illeggibile: uso i valori predefiniti")
    return _deep_merge(DEFAULT_SETTINGS, {})


SETTINGS: dict = load_settings()


def save_settings() -> None:
    try:
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        dump = {k: v for k, v in SETTINGS.items()
                if k not in ("context_notes", "context_folder")}
        SETTINGS_FILE.write_text(
            json.dumps(dump, indent=2, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        logging.getLogger("iacm.config").exception(
            "salvataggio settings non riuscito")


# --- Cartelle temporanee ----------------------------------------------------
def get_temp_root() -> Path:
    root = SETTINGS.get("temp_root") or DEFAULT_SETTINGS["temp_root"]
    return Path(os.path.expandvars(str(root)))


def new_session_dir() -> Path:
    """Crea e restituisce la cartella temporanea della sessione corrente."""
    name = "session_" + time.strftime("%Y%m%d_%H%M%S")
    path = get_temp_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge_temp_sessions(root: Path | None = None,
                        quiet: bool = False) -> int:
    """Elimina tutte le voci temporanee create dall'app (session_*, selftest_*).

    Viene chiamata all'avvio dell'app, a ogni nuova registrazione e alla
    chiusura: le sessioni non salvate esplicitamente dall'utente non
    sopravvivono al riavvio (il video/audio è temporaneo per scelta).
    Non tocca altri file presenti nella cartella scelta dall'utente.
    Ritorna il numero di voci rimosse.
    """
    target = Path(root) if root else get_temp_root()
    removed = 0
    freed = 0
    if not target.is_dir():
        return 0
    for path in list(target.iterdir()):
        if not path.name.startswith(("session_", "selftest_")):
            continue
        try:
            size = _tree_size(path)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            if not path.exists():
                removed += 1
                freed += size
        except Exception:
            logging.getLogger("iacm.config").warning(
                "rimozione temporanea fallita: %s", path, exc_info=True)
    if removed and not quiet:
        logging.getLogger("iacm.config").info(
            "pulizia temporanee: %d voci rimosse (%.1f MB liberati)", removed,
            freed / (1024 * 1024))
    return removed


def _tree_size(path: Path) -> int:
    """Byte occupati da un file o da una cartella (0 se illeggibile)."""
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return 0


# --- Chiave API Gemini ------------------------------------------------------
ENV_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")


def _opencode_auth_google_key() -> str | None:
    candidates = [
        Path.home() / ".local" / "share" / "opencode" / "auth.json",
        Path(os.environ.get("APPDATA", "")) / "opencode" / "auth.json",
    ]
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                key = (data.get("google") or {}).get("key")
                if isinstance(key, str) and key.strip():
                    return key.strip()
        except Exception:
            continue
    return None


def load_api_key() -> str | None:
    for name in ENV_KEY_NAMES:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return _opencode_auth_google_key()


def mask_key(key: str | None) -> str:
    if not key:
        return "nessuna"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}…{key[-4:]}"


# --- Credenziali per provider ----------------------------------------------
def _is_local(base_url: str) -> bool:
    b = (base_url or "").lower()
    return any(h in b for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def stt_key(cfg: dict | None = None) -> str | None:
    cfg = SETTINGS["stt"] if cfg is None else cfg
    key = (cfg.get("api_key") or "").strip()
    if key:
        return key
    if cfg.get("type") == "gemini":
        return load_api_key()
    return None


def text_key(cfg: dict | None = None, stt_cfg: dict | None = None) -> str | None:
    cfg = SETTINGS["text"] if cfg is None else cfg
    key = (cfg.get("api_key") or "").strip()
    if key:
        return key
    if cfg.get("type") == "copilot":
        # GitHub Copilot usa il proprio login (copilot.json), non l'api_key
        try:
            from .ai import copilot
            return "copilot" if copilot.is_logged_in() else None
        except Exception:
            return None
    if cfg.get("type") == "gemini":
        fallback = stt_key(SETTINGS["stt"] if stt_cfg is None else stt_cfg)
        if fallback:
            return fallback
        return load_api_key()
    return None


def missing_credentials(settings: dict | None = None) -> list[str]:
    """Messaggi per le credenziali mancanti (lista vuota = tutto configurato)."""
    s = SETTINGS if settings is None else settings
    msgs: list[str] = []
    for label, cfg, key in (
        ("trascrizione", s["stt"], stt_key(s["stt"])),
        ("testo (riassunto/minute)", s["text"], text_key(s["text"], s["stt"])),
    ):
        typ = cfg.get("type", "gemini")
        if typ == "offline":
            try:
                import faster_whisper              # noqa: F401
            except ImportError:
                msgs.append(f"{label}: pacchetto 'faster-whisper' non "
                            "installato (pip install faster-whisper)")
        elif typ == "copilot":
            if not key:      # text_key() vale «collegato» solo se copilot.json
                msgs.append(label + ": GitHub Copilot non collegato — in "
                            "Configurazione → Testo premi «🪪 Accedi con "
                            "GitHub Copilot» (nemmeno serve una chiave)")
        elif typ == "gemini":
            if not key:
                # L'accesso di Antigravity / Gemini CLI (OAuth Google) non è
                # una chiave API: serve una chiave AI Studio (gratuita).
                msgs.append(f"{label}: manca la chiave API Gemini — il login "
                            "Google di Antigravity non vale come chiave; "
                            "creala gratis su aistudio.google.com/apikey e "
                            "incollala in Configurazione (o variabile "
                            "GEMINI_API_KEY)")
        else:
            base = (cfg.get("base_url") or "").strip()
            model = (cfg.get("model") or "").strip()
            api_key = (cfg.get("api_key") or "").strip()
            if not base:
                msgs.append(f"{label}: manca l'URL dell'endpoint OpenAI-compat")
            if not model:
                msgs.append(f"{label}: manca il nome del modello")
            if not api_key and not _is_local(base):
                msgs.append(f"{label}: manca la chiave dell'endpoint")
    return msgs


# --- Logging ----------------------------------------------------------------
_LOG_READY = False


def setup_logging() -> None:
    """Log su file SOLO se «log_enabled» — default: disattivato.

    Con il log spento (default) non viene creato alcun file e non si
    occupa spazio su disco. Il flag si cambia in Configurazione e ha
    effetto immediato al salvataggio.
    """
    global _LOG_READY
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, (logging.handlers.RotatingFileHandler,
                          logging.NullHandler)):
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
    _LOG_READY = False
    if not bool(SETTINGS.get("log_enabled", False)):
        root.addHandler(logging.NullHandler())   # niente output, niente file
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        root.setLevel(logging.INFO)
        handler = logging.handlers.RotatingFileHandler(
            LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        root.addHandler(handler)
        _LOG_READY = True
        logging.getLogger("iacm").info(
            "logging avviato v%s file=%s", APP_VERSION, LOG_DIR / "app.log")
    except Exception:
        _LOG_READY = False


# --- Cattura audio ----------------------------------------------------------
FRAME_MS = 30                  # frame di analisi VAD
PREROLL_MS = 300               # pre-roll conservato prima dell'inizio parlato
SILENCE_END_MS = 1000          # silenzio necessario a chiudere un segmento
MIN_SPEECH_MS = 400            # parlato minimo per generare un segmento
MAX_SEGMENT_S = 6.0            # durata massima forzata di un segmento
MIN_SEGMENT_S = 0.8            # parlato minimo in secondi (altrimenti scartato)

# Soglia assoluta minima (int16, ~-47 dBFS). Era 600: con chiamate a volume
# basso (picchi ~-27 dBFS) il parlato non superava mai la soglia e non veniva
# trascritto nulla; ora decide soprattutto il rumore di fondo adattivo.
VAD_MIN_RMS = 150
VAD_NOISE_MULT = 3.0           # multipli del rumore di fondo
VAD_ON_FRAMES = 3              # frame consecutivi per aprire il parlato
VAD_HANG_FRAMES = 24           # frame di silenzio per chiudere (~0.7s)

MIX_TICK_MS = 100              # periodo del mixer
MAX_BUFFER_S = 10              # buffered massimo per sorgente prima del drop

# --- Segmentazione per l'AI -------------------------------------------------
SEGMENT_MIN_S = 1.0            # segmenti più corti di così vengono fusi/ignorati


# --- API --------------------------------------------------------------------
# 2 worker bastano: con segmenti di ~6 s e chiamate di ~2 s il collo di
# bottiglia e' la rete, non il parallelismo. 4 worker aumentavano solo le
# richieste concorrenti verso Gemini (errori "high demand").
PIPELINE_WORKERS = 2             # segmenti transcodificati in parallelo
MIN_CALL_INTERVAL_S = 0.2        # minimo intervallo tra due chiamate API
REQUEST_TIMEOUT_S = 90
# Generazione (riassunto/minute): la risposta puo' essere lunga e il modello
# ragiona prima di rispondere, quindi ha un timeout piu' generoso del singolo
# segmento audio.
GENERATE_TIMEOUT_S = 300
# Tetto massimo di attesa del pulsante FERMA: la coda viene completata
# (trascrizione + traduzione) e poi la sessione si chiude. Passato questo
# tempo i segmenti rimasti vengono annullati con un avviso.
STOP_DRAIN_S = 300
# Tentativi automatici solo per gli errori TRANSITORI (rete/5xx). Gli errori
# definitivi (modello inesistente, chiave errata, quota 429) passano subito
# all'utente con il dialog di ripresa.
MAX_RETRIES = 3
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 10.0
# Segmenti tenguti in coda massimo mentre la trascrizione e' in pausa
# per un errore (~6s l'uno: 200 = ~20 min di call, ~40 MB di PCM).
MAX_PAUSED_SEGMENTS = 200
