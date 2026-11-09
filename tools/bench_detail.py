import sys, time
sys.path.insert(0, ".")
from app.ai import offline

en = open(r"C:\Temp\bench\en.wav", "rb").read()
it = open(r"C:\Temp\bench\it.wav", "rb").read()

print("=== 1. caricamento modelli (prima esecuzione) ===")
t = time.monotonic()
offline._whisper("small")
print("  whisper small load: %6.0f ms" % ((time.monotonic() - t) * 1000))

t = time.monotonic()
out = offline._translate("Hello everyone, welcome to the meeting.", "en")
print("  argos en->it COLD : %6.0f ms  %s" % ((time.monotonic() - t) * 1000, out[:50]))

print("\n=== 2. a caldo, 10 misurazioni ===")
for label, wav in (("IT", it), ("EN", en)):
    ts = []
    for i in range(10):
        t = time.monotonic()
        r = offline.transcribe({"model": "small"}, wav, duration=6.0,
                               start=0.0, bilingual=True)
        ts.append((time.monotonic() - t) * 1000)
    print("  %s trascrizione+traduzione: min %4.0f  media %4.0f  max %4.0f ms"
          % (label, min(ts), sum(ts) / len(ts), max(ts)))

print("\n=== 3. solo trascrizione Whisper (senza argos) ===")
model = offline._whisper("small")
for label, wav in (("IT", it), ("EN", en)):
    audio = offline._wav_f32(wav)
    ts = []
    for i in range(10):
        t = time.monotonic()
        segs, info = model.transcribe(audio, language=None, vad_filter=True,
                                      beam_size=1,
                                      condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segs)
        ts.append((time.monotonic() - t) * 1000)
    print("  %s whisper pura (lang=%s): min %4.0f  media %4.0f  max %4.0f ms"
          % (label, info.language, min(ts), sum(ts) / len(ts), max(ts)))

print("\n=== 4. solo traduzione argos (a caldo) ===")
txt = ("Hello everyone, welcome to the meeting. Today we are going to "
       "discuss the quarterly results and the new project plan.")
ts = []
for i in range(10):
    t = time.monotonic()
    offline._translate(txt, "en")
    ts.append((time.monotonic() - t) * 1000)
print("  argos en->it      : min %4.0f  media %4.0f  max %4.0f ms"
      % (min(ts), sum(ts) / len(ts), max(ts)))
