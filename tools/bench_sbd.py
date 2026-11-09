"""Confronto latenza/qualita' traduzione: Stanza vs MiniSBD.

Uso:  python tools\\bench_sbd.py stanza|minisbd
"""
import sys, time, os

WHICH = (sys.argv[1] if len(sys.argv) > 1 else "minisbd").lower()
TXT = ("Hello everyone, welcome to the meeting. Today we are going to "
       "discuss the quarterly results and the new project plan. We will "
       "start with the sales figures from the last quarter, then move on "
       "to the new project plan for next year. Please feel free to "
       "interrupt me if you have any questions at any point during this "
       "presentation, and I will be happy to answer them.")

sys.path.insert(0, ".")
from app.ai import offline
from argostranslate import settings as s

offline._argos_setup()                      # imposta MiniSBD come fa l'app
if WHICH == "stanza":
    s.chunk_type = s.ChunkType.ARGOSTRANSLATE
print(f"chunk_type in uso: {s.chunk_type.name}")

ts = []
for i in range(5):
    t = time.monotonic()
    out = offline._translate(TXT, "en")
    ts.append((time.monotonic() - t) * 1000)

print(f"primo caricamento : {ts[0]:.0f} ms")
print(f"a caldo           : min {min(ts[1:]):.0f}  media "
      f"{sum(ts[1:]) / len(ts[1:]):.0f}  max {max(ts[1:]):.0f} ms")
print(f"lunghezza output  : {len(out)} car")
print(f"chunk_type dopo   : {s.chunk_type.name}")
print(f"testo             : {out[:150]}")
sys.exit(0 if out.strip() else 1)
