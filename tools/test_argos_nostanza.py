"""Simula l'exe: stanza/spacy NON disponibili. La traduzione funziona ancora?"""
import sys, time, importlib.abc, importlib.machinery

BLOCCATI = {"stanza", "spacy", "spacy.lang", "torch", "torchvision"}


class No(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in BLOCCATI:
            raise ImportError(f"[sim] {fullname} non disponibile (come nell'exe)")
        return None


sys.meta_path.insert(0, No())
sys.path.insert(0, ".")

from app.ai import offline

print("argos importabile:", end=" ")
try:
    from argostranslate import translate as t
    print("si")
except Exception as e:
    print("NO ->", e)

txt = ("Hello everyone, welcome to the meeting. Today we are going to "
       "discuss the quarterly results and the new project plan.")
t0 = time.monotonic()
out = offline._translate(txt, "en")
dt = (time.monotonic() - t0) * 1000
print(f"\ntraduzione en->it in {dt:.0f} ms:")
print(f"  '{(out or '')[:160]}'")
print("\nRISULTATO:", "FUNZIONA" if out.strip() else "NON FUNZIONA (vuoto)")
sys.exit(0 if out.strip() else 1)
