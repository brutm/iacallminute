"""Benchmark latenza trascrizione + traduzione (Gemini vs offline)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config                      # noqa: E402
from app.ai import gemini, service          # noqa: E402

EN = Path(r"C:\Temp\bench\en.wav").read_bytes()
IT = Path(r"C:\Temp\bench\it.wav").read_bytes()
DUR = 6.0
settings = config.load_settings()

MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]


def bench_gemini() -> None:
    key = config.stt_key(settings["stt"])
    print("\n=== GEMINI: trascrizione+traduzione (audio EN, bilingual) ===")
    print(f"{'modello':<26} {'ms':>7}  lingua | testo")
    for model in MODELS:
        t0 = time.monotonic()
        try:
            r = gemini.transcribe_segment(key, model, EN, duration=DUR,
                                          start=0.0, bilingual=True)
        except Exception as exc:
            dt = (time.monotonic() - t0) * 1000
            print(f"{model:<26} {dt:7.0f}  ERRORE: {str(exc)[:80]}")
            continue
        dt = (time.monotonic() - t0) * 1000
        print(f"{model:<26} {dt:7.0f}  {r['language']:<3} | "
              f"{(r['italian'] or r['original'])[:60]}")

    print("\n=== GEMINI: solo trascrizione (audio IT) ===")
    print(f"{'modello':<26} {'ms':>7}  lingua | testo")
    for model in MODELS:
        t0 = time.monotonic()
        try:
            r = gemini.transcribe_segment(key, model, IT, duration=DUR,
                                          start=0.0, bilingual=False)
        except Exception as exc:
            dt = (time.monotonic() - t0) * 1000
            print(f"{model:<26} {dt:7.0f}  ERRORE: {str(exc)[:80]}")
            continue
        dt = (time.monotonic() - t0) * 1000
        print(f"{model:<26} {dt:7.0f}  {r['language']:<3} | "
              f"{(r['italian'] or r['original'])[:60]}")


def bench_offline() -> None:
    from app.ai import offline
    print("\n=== OFFLINE: faster-whisper + argos ===")
    stt = {"model": "small"}
    for label, wav in (("EN", EN), ("IT", IT)):
        t0 = time.monotonic()
        try:
            r = offline.transcribe(stt, wav, duration=DUR, start=0.0,
                                   bilingual=True)
        except Exception as exc:
            print(f"{label}: ERRORE {exc}")
            continue
        dt = (time.monotonic() - t0) * 1000
        print(f"{label:<3} ({'prima esecuzione = caricamento' if label == 'EN' else 'a caldo'}):"
              f" {dt:7.0f} ms | lang={r['language']} | "
              f"{(r['italian'] or r['original'])[:70]}")


def bench_text() -> None:
    print("\n=== GEMINI: testo (riassunto/minute) ===")
    for model in MODELS:
        t0 = time.monotonic()
        try:
            key = config.text_key(settings["text"], settings.get("stt"))
            out = gemini.complete_text(key, model,
                                  "Rispondi in italiano.",
                                  "Scrivi due frasi sul risultato trimestrale.")
        except Exception as exc:
            dt = (time.monotonic() - t0) * 1000
            print(f"{model:<26} {dt:7.0f}  ERRORE: {str(exc)[:80]}")
            continue
        dt = (time.monotonic() - t0) * 1000
        print(f"{model:<26} {dt:7.0f}  {out[:60]}")


if __name__ == "__main__":
    print(f"key: {config.mask_key(config.stt_key(settings['stt']))}")
    print(f"settings stt: {json.dumps(settings['stt'])}")
    if "--text" in sys.argv:
        bench_text()
    elif "--offline" in sys.argv:
        bench_offline()
    elif "--gemini-it" in sys.argv:
        key = config.stt_key(settings["stt"])
        print("\n=== GEMINI: solo trascrizione (audio IT) ===")
        for model in MODELS:
            t0 = time.monotonic()
            try:
                r = gemini.transcribe_segment(key, model, IT, duration=DUR,
                                              start=0.0, bilingual=False)
            except Exception as exc:
                print(f"{model:<26} {(time.monotonic()-t0)*1000:7.0f}  ERRORE: {str(exc)[:80]}")
                continue
            print(f"{model:<26} {(time.monotonic()-t0)*1000:7.0f}  {r['language']} | "
                  f"{(r['italian'] or r['original'])[:60]}")
    else:
        bench_gemini()
