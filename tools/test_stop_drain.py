"""FERMA deve ASPETTARE: i segmenti gia' in coda vengono trascritti e
tradotti prima di chiudere (non piu' «sessione interrotta prima della
trascrizione»).

Provider finto: nessuna rete, tempi noti.
"""
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import config  # noqa: E402
from app.ai import service  # noqa: E402
from app.ai.errors import ProviderError  # noqa: E402
from app.transcription.pipeline import Pipeline  # noqa: E402

log = logging.getLogger("iacm.test")

DELAY = 0.6            # secondi di "trascrizione" per segmento
FAILS = []             # segmenti che devono fallire al primo tentativo


def fake_transcribe(settings, wav_bytes, *, duration, start, bilingual):
    time.sleep(DELAY)
    if start in FAILS:
        FAILS.remove(start)
        raise ProviderError("errore di rete finto", 0, True)
    return {"language": "it",
            "italian": f"IT segmento {start:.0f}",
            "original": f"EN segmento {start:.0f}" if bilingual else ""}


def main() -> int:
    service.transcribe_segment = fake_transcribe
    ok = 0

    results: list[dict] = []
    settings = {"stt": {"type": "gemini", "model": "finto", "api_key": ""},
                "text": {"type": "gemini", "model": "finto", "api_key": ""}}
    p = Pipeline(settings, on_result=results.append, on_status=lambda m: None,
                 on_error=lambda m: None, on_fault=lambda i: None)
    p.bilingual = True
    p.start()
    audio = np.zeros(int(3 * config.TARGET_RATE), dtype=np.int16)
    audio[: config.TARGET_RATE] = 1000

    # 5 segmenti: il primo fallisce la prima volta (retry), gli altri no
    FAILS.append(0.0)
    for i in range(5):
        p.submit(i * 3.0, audio.copy())

    # 1) stop mentre il primo segmento e' in lavorazione e 4 in coda
    time.sleep(DELAY / 2)
    t0 = time.monotonic()
    p.stop(drain=True, timeout=60)
    elapsed = time.monotonic() - t0

    # 2) tutti i segmenti devono essere stati trascritti: nessuno annullato
    starts = sorted(r["start"] for r in results)
    print("1) segmenti elaborati: %s (in %.1fs)" % (starts, elapsed))
    assert starts == [0.0, 3.0, 6.0, 9.0, 12.0], starts
    ok += 1

    # 3) nessun errore "interrotta prima della trascrizione"
    bad = [r["error"] for r in results if r["error"]]
    print("2) errori: %s" % (bad or "nessuno"))
    assert not any("interrotta prima" in str(e) for e in bad), bad
    ok += 1

    # 4) il segmento che fallisce e' stato RITENTATO (testa del retry)
    r0 = [r for r in results if r["start"] == 0.0][0]
    assert not r0["error"], r0["error"]
    assert r0["italian"] == "IT segmento 0", r0
    ok += 1
    print("3) retry durante la chiusura ok")

    # 5) traduzione presente (bilingual) su tutti i segmenti
    assert all(r["original"] for r in results), results
    ok += 1
    print("4) traduzione mantenuta ok")

    # 6) lo stop ha davvero atteso: piu' del tempo di un segmento
    assert elapsed >= DELAY, elapsed
    ok += 1
    print("5) attesa reale durante la chiusura ok")

    # 7) drain=False annulla subito (fermata immediata a richiesta)
    results2: list[dict] = []
    p2 = Pipeline(settings, on_result=results2.append,
                  on_status=lambda m: None, on_error=lambda m: None,
                  on_fault=lambda i: None)
    p2.bilingual = False
    p2.start()
    for i in range(3):
        p2.submit(i * 3.0, audio.copy())
    time.sleep(0.1)
    t1 = time.monotonic()
    p2.stop(drain=False, timeout=30)
    fast = time.monotonic() - t1
    print("6) drain=False: %d risultati in %.2fs" % (len(results2), fast))
    # non puo' interrompere la chiamata gia' in volo, ma NON deve svuotare
    # tutta la coda: deve essere piu' rapido del drain completo
    assert fast < 3 * DELAY, fast
    ok += 1
    print("7) drain=False (non svuota la coda) ok")

    # 8) nessun thread della pipeline rimasto vivo
    alive = [t.name for t in threading.enumerate()
             if t.name.startswith("pipeline-") and t.is_alive()]
    assert not alive, alive
    ok += 1
    print("8) thread pipeline chiusi ok")

    print("PASS %d/7 test_stop_drain" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())