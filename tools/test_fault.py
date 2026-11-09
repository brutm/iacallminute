"""Test della pausa/ripresa della pipeline su errore definitivo.

Il provider STT e' finto (nessuna rete): modello «buono» risponde subito,
modello «rotto» solleva un errore definitivo (404). Prima si usava Gemini
reale: la latenza di rete rendeva l'attesa Dell'assert flaky.
"""
import sys, time, logging
sys.path.insert(0, ".")
import numpy as np
from app import config
from app.ai import service
from app.ai.errors import ProviderError
from app.transcription.pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

results, faults, statuses = [], [], []
UNKNOWN = "gemini-9.9-inesistente"

good = {"type": "gemini", "model": "gemini-3.5-flash-lite", "api_key": ""}
bad = {"type": "gemini", "model": UNKNOWN, "api_key": ""}

settings = {"stt": dict(bad), "text": dict(good)}


def fake_stt(settings, wav_bytes, *, duration, start, bilingual):
    time.sleep(0.05)
    model = ((settings["stt"].get("model") or "").strip().lower())
    if model.startswith("gemini-3"):
        return {"language": "it", "italian": f"ok {start:.0f}",
                "original": f"ecc {start:.0f}"}
    raise ProviderError(
        f"HTTP 404: models/{model} is not found for API version v1beta",
        404, False)


service.transcribe_segment = fake_stt


def on_result(r):
    results.append(r)
    print("  RESULT seq-ok start=%.1f err=%s text=%s" % (
        r["start"], r["error"], (r["italian"] or r["original"])[:40]))


def on_fault(info):
    faults.append(info)
    print("  FAULT", {k: info[k] for k in ("error", "count", "model")})


p = Pipeline(settings, on_result=on_result, on_status=statuses.append,
             on_error=print, on_fault=on_fault)
p.bilingual = True
p.start()

audio = np.zeros(int(6 * config.TARGET_RATE), dtype=np.int16)
audio[: config.TARGET_RATE] = 1000

print("\n--- 1. segmenti con modello ROTTO (deve mettersi in pausa) ---")
for i in range(3):
    p.submit(i * 6.0, audio.copy())
time.sleep(6)
print("paused=%s faults=%d queue=%d results=%d" % (
    p.paused, len(p.faults), p.queue.qsize(), len(results)))

print("\n--- 2. altri segmenti mentre è in pausa (restano in coda) ---")
for i in range(3, 6):
    p.submit(i * 6.0, audio.copy())
time.sleep(2)
print("paused=%s faults=%d queue=%d results=%d" % (
    p.paused, len(p.faults), p.queue.qsize(), len(results)))
assert p.paused, "la pipeline doveva essere in pausa"
assert len(results) == 0, "nessun risultato emesso finché in pausa"

print("\n--- 3. cambio modello + ripresa ---")
settings["stt"] = dict(good)
n = p.resume("retry")
print("ripristinati:", n)
deadline = time.time() + 120
while len(results) < 6 and time.time() < deadline:
    time.sleep(0.5)
print("paused=%s results=%d errors=%d" % (
    p.paused, len(results), sum(1 for r in results if r["error"])))
assert not p.paused, "la pipeline doveva riprendere"
assert len(results) == 6, "attesi 6 risultati, ne ho %d" % len(results)
assert all(not r["error"] for r in results), "errori inattesi: %s" % results
assert [r["start"] for r in results] == [i * 6.0 for i in range(6)], \
    "ordine dei segmenti non conservato"

print("\n--- 4. nuovo errore + salti ---")
settings["stt"] = dict(bad)
p.submit(36.0, audio.copy())
deadline = time.time() + 30
while not p.paused and time.time() < deadline:
    time.sleep(0.2)
assert p.paused, "secondo errore: doveva mettersi in pausa"
p.submit(42.0, audio.copy())
time.sleep(1)
settings["stt"] = dict(good)
n = p.resume("skip")
print("saltati:", n)
deadline = time.time() + 60
while len(results) < 8 and time.time() < deadline:
    time.sleep(0.5)
print("results=%d (attesi 8)" % len(results))
assert len(results) == 8, "attesi 8 risultati, ne ho %d" % len(results)

print("\n--- 5. stop durante una pausa (drain: completa la coda) ---")
settings["stt"] = dict(bad)
p.submit(48.0, audio.copy())
deadline = time.time() + 30
while not p.paused and time.time() < deadline:
    time.sleep(0.2)
p.submit(54.0, audio.copy())
time.sleep(1)
t0 = time.monotonic()
p.stop(drain=True, timeout=120)
took = time.monotonic() - t0
print("stop (drain) in %.1fs, results=%d" % (took, len(results)))
# il segmento in coda viene elaborato, non buttato: niente «interrotta»
annullati = [r for r in results
             if r["error"] and "interrotta prima" in str(r["error"])]
assert not annullati, annullati
assert len(results) >= 10, "segmenti in coda persi: %d risultati" % len(results)
assert took < 120, "drain troppo lento"

print("\nTUTTI I TEST PASSATI")
