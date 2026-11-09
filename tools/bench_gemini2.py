import sys, time
sys.path.insert(0, ".")
from app import config
from app.ai import gemini

s = config.load_settings()
key = config.stt_key(s["stt"])
en = open(r"C:\Temp\bench\en.wav", "rb").read()
it = open(r"C:\Temp\bench\it.wav", "rb").read()

MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash",
          "gemini-flash-lite-latest", "gemini-2.5-flash-lite",
          "gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest"]

N = 5
print("=== GEMINI: audio EN 6s, bilingual (trascrizione + traduzione) ===")
print("%-26s %8s %8s %8s  ok" % ("modello", "min ms", "media", "max ms"))
for m in MODELS:
    ts, err = [], 0
    for i in range(N):
        t = time.monotonic()
        try:
            gemini.transcribe_segment(key, m, en, duration=6.0, start=0.0,
                                      bilingual=True)
        except Exception as e:
            err += 1
            ts.append((time.monotonic() - t) * 1000)
            continue
        ts.append((time.monotonic() - t) * 1000)
    print("%-26s %8.0f %8.0f %8.0f  %d/%d"
          % (m, min(ts), sum(ts) / len(ts), max(ts), N - err, N))
    sys.stdout.flush()

print("\n=== GEMINI: audio IT 6s, solo trascrizione ===")
print("%-26s %8s %8s %8s  ok" % ("modello", "min ms", "media", "max ms"))
for m in MODELS:
    ts, err = [], 0
    for i in range(N):
        t = time.monotonic()
        try:
            gemini.transcribe_segment(key, m, it, duration=6.0, start=0.0,
                                      bilingual=False)
        except Exception:
            err += 1
            ts.append((time.monotonic() - t) * 1000)
            continue
        ts.append((time.monotonic() - t) * 1000)
    print("%-26s %8.0f %8.0f %8.0f  %d/%d"
          % (m, min(ts), sum(ts) / len(ts), max(ts), N - err, N))
    sys.stdout.flush()
