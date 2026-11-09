import sys, time
sys.path.insert(0, ".")
from app import config
from app.ai import offline

en = open(r"C:\Temp\bench\en.wav", "rb").read()
it = open(r"C:\Temp\bench\it.wav", "rb").read()

print("=== OFFLINE: whisper small + argos (bilingual) ===")
sys.stdout.flush()
for label, wav in (("EN-1 (caricamento modello)", en), ("EN-2 (a caldo)", en),
                   ("IT (a caldo)", it)):
    t0 = time.monotonic()
    try:
        r = offline.transcribe({"model": "small"}, wav, duration=6.0,
                               start=0.0, bilingual=True)
        out = r["italian"] or r["original"]
        print(f"{label:<28}{(time.monotonic()-t0)*1000:8.0f} ms  lang={r['language']}  {out[:60]}")
    except Exception as e:
        print(f"{label:<28}{(time.monotonic()-t0)*1000:8.0f} ms  ERR {str(e)[:120]}")
    sys.stdout.flush()

print("\n=== OFFLINE: solo trascrizione (no traduzione) ===")
sys.stdout.flush()
for label, wav in (("IT", it), ("EN", en)):
    t0 = time.monotonic()
    r = offline.transcribe({"model": "small"}, wav, duration=6.0,
                           start=0.0, bilingual=False)
    out = r["italian"] or r["original"]
    print(f"{label:<28}{(time.monotonic()-t0)*1000:8.0f} ms  lang={r['language']}  {out[:60]}")
    sys.stdout.flush()

print("\n=== OFFLINE: whisper base e tiny ===")
sys.stdout.flush()
for size in ("base", "tiny"):
    t0 = time.monotonic()
    r = offline.transcribe({"model": size}, en, duration=6.0, start=0.0, bilingual=True)
    out = r["italian"] or r["original"]
    print(f"{size:<28}{(time.monotonic()-t0)*1000:8.0f} ms  lang={r['language']}  {out[:60]}")
    sys.stdout.flush()
