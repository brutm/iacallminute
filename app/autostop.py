"""Arresto automatico della registrazione (audio e, se attivo, video).

Logica pura (senza Tk) così è testabile da sola:

- «after»: la sessione si ferma dopo N minuti di durata complessiva;
- «at»: la sessione si ferma all'orario HH:MM (oggi, o domani se l'orario
  è già passato al momento dell'avvio).

Le scadenze sono espresse in ``time.monotonic()``: non risentono dei
cambi d'ora del sistema durante la call.
"""
from __future__ import annotations

import datetime as _dt
import re

MODES: list[tuple[str, str]] = [
    ("off", "Disattivato"),
    ("after", "Dopo (minuti)"),
    ("at", "Alle ore (HH:MM)"),
]
MODE_LABELS = {k: v for k, v in MODES}
LABEL_MODES = {v: k for k, v in MODES}

MIN_MINUTES = 1
MAX_MINUTES = 24 * 60
WARN_BEFORE_S = 60          # avviso «manca 1 minuto»
GRACE_S = 60                # se la nuova scadenza è già passata


def parse_minutes(text) -> int | None:
    """Minuti interi validi (1..1440) oppure None."""
    try:
        val = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    if MIN_MINUTES <= val <= MAX_MINUTES:
        return val
    return None


_CLOCK_RE = re.compile(r"^\s*(\d{1,2})\s*[:.,h]?\s*(\d{2})\s*$")


def parse_clock(text) -> tuple[int, int] | None:
    """Accetta «18:30», «18.30», «18h30», «1830», «9:05» → (h, m)."""
    m = _CLOCK_RE.match(str(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


def seconds_until(h: int, m: int, now: _dt.datetime) -> float:
    """Secondi da ``now`` al prossimo HH:MM (domani se già passato)."""
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    return (target - now).total_seconds()


def compute_deadline(mode: str, value, *, start_mono: float,
                     now_mono: float,
                     now_dt: _dt.datetime | None = None) -> float | None:
    """Scadenza in tempo monotonic, oppure None (timer spento/non valido).

    ``start_mono`` è l'istante di avvio della sessione: con «after» la
    durata conta dall'inizio della registrazione, anche se il valore
    viene cambiato a sessione in corso.
    """
    if mode == "after":
        minutes = parse_minutes(value)
        if minutes is None:
            return None
        return start_mono + minutes * 60.0
    if mode == "at":
        hm = parse_clock(value)
        if hm is None:
            return None
        now_dt = now_dt or _dt.datetime.now()
        return now_mono + seconds_until(hm[0], hm[1], now_dt)
    return None


def format_remaining(seconds: float) -> str:
    s = max(0, int(seconds + 0.999))      # arrotonda per eccesso
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def describe(mode: str, value) -> str:
    """Descrizione breve per la UI («si ferma dopo 45 min» …)."""
    if mode == "after":
        minutes = parse_minutes(value)
        if minutes is None:
            return "minuti non validi (1–1440)"
        h, mi = divmod(minutes, 60)
        dur = (f"{h} h {mi:02d} min" if h and mi else
               f"{h} h" if h else f"{mi} min")
        return f"si ferma dopo {dur} di registrazione"
    if mode == "at":
        hm = parse_clock(value)
        if hm is None:
            return "orario non valido (es. 18:30)"
        return f"si ferma alle {hm[0]:02d}:{hm[1]:02d}"
    return "nessun arresto automatico"
