import sys, time
sys.path.insert(0, ".")
from app import config
from app.ai import gemini

s = config.load_settings()
key = config.stt_key(s["stt"])
en = open(r"C:\Temp\bench\en.wav", "rb").read()
it = open(r"C:\Temp\bench\it.wav", "rb").read()
models = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite",
          "gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]

def run(label, wav, bilingual):
    print(f"\n=== {label} ===")
    sys.stdout.flush()
    for m in models:
        for i in range(2):
            t0 = time.monotonic()
            try:
                r = gemini.transcribe_segment(key, m, wav, duration=6.0,
                                              start=0.0, bilingual=bilingual)
                out = r["italian"] or r["original"]
                print(f"{m:<26}{(time.monotonic()-t0)*1000:7.0f} ms  lang={r['language']}  {out[:55]}")
            except Exception as e:
                print(f"{m:<26}{(time.monotonic()-t0)*1000:7.0f} ms  ERR {str(e)[:90]}")
            sys.stdout.flush()

run("EN audio, bilingual (trascrizione + traduzione)", en, True)
run("IT audio, no bilingual (solo trascrizione)", it, False)
