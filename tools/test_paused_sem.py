"""Verifica il bilanciamento di _PausedSemaphore nella pausa/retry/stop."""
import queue as _q
import sys
import threading

sys.path.insert(0, ".")

from app import config
from app.transcription.pipeline import Pipeline, _PausedSemaphore

MAX = config.MAX_PAUSED_SEGMENTS
FAIL = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def job(seq, acquired=True):
    return {"seq": seq, "start": float(seq), "duration": 1.0,
            "bilingual": False, "_paused_acquired": acquired}


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except _q.Empty:
            return out


print(f"MAX_PAUSED_SEGMENTS={MAX}\n")

print("1) acquire non bloccante")
sem = _PausedSemaphore(MAX)
n = sum(1 for _ in range(5) if sem.acquire(blocking=False))
check(n == 5 and len(sem) == 5, f"5 acquire -> conteggio {len(sem)}")

print("\n2) limite massimo")
sem2 = _PausedSemaphore(3)
n = 0
while sem2.acquire(blocking=False):
    n += 1
check(n == 3, f"accetta {n} job (atteso 3)")
check(not sem2.acquire(blocking=False), "6° acquire rifiutato")

print("\n3) acquire bloccante")
sem3 = _PausedSemaphore(1)
sem3.acquire(blocking=False)          # pieno
got = []
t = threading.Thread(target=lambda: got.append(sem3.acquire(timeout=0.3)))
t.start()
t.join()
check(got == [False], f"timeout -> {got[0] if got else '?'} (atteso False)")
sem3.release()                        # uno slot si libera
ok = sem3.acquire(blocking=True, timeout=0.5)
check(ok is True, f"dopo release ritorna {ok} (atteso True)")
check(len(sem3) == 1,
      f"conteggio incrementato: {len(sem3)} (prima del fix restava 0)")

print("\n4) Pipeline.resume('retry'): nessun release in piu'")
p = Pipeline({"stt": {"type": "gemini"}}, on_result=lambda r: None)
p._resume.clear()                     # pausa
n = sum(1 for _ in range(5) if p._paused_sem.acquire(blocking=False))
check(n == 5, f"5 job sottomessi durante la pausa -> {len(p._paused_sem)}")
for i in range(2):                    # 2 job falliscono -> finally
    p.faults.append({"job": job(i), "error": "errore simulato"})
    p._paused_sem.release()
check(len(p._paused_sem) == 3,
      f"dopo il finally dei 2 falliti -> {len(p._paused_sem)} (atteso 3)")
n = p.resume("retry")
residui = drain(p.queue)
check(n == 2, f"resume('retry') -> {n} (atteso 2)")
check(len(p._paused_sem) == 3,
      f"contatore invariato: {len(p._paused_sem)} (prima del fix: 1)")
check(len(residui) == 2, f"2 job rimessi in coda: {len(residui)}")
check(all(not j.get("_paused_acquired") for j in residui),
      "flag _paused_acquired azzerato sui job riproposti")
for j in residui:                     # come il loro future _worker.finally
    if j.get("_paused_acquired"):
        p._paused_sem.release()
check(len(p._paused_sem) == 3,
      f"il finally non decrementa due volte: {len(p._paused_sem)}")

print("\n5) Pipeline.stop(): nessun release in piu'")
p2 = Pipeline({"stt": {"type": "gemini"}}, on_result=lambda r: None)
p2._resume.clear()
for _ in range(4):
    p2._paused_sem.acquire(blocking=False)
p2.faults.append({"job": job(0), "error": "errore simulato"})
p2._paused_sem.release()              # finally del worker
before = len(p2._paused_sem)
p2.start()
p2.stop(drain=True, timeout=30)
check(len(p2._paused_sem) == before,
      f"contatore dopo stop: {len(p2._paused_sem)} (atteso {before})")
check(not p2.is_alive(), "thread pipeline terminato")

print("\n" + ("TUTTI I TEST PASSATI" if not FAIL
              else f"{len(FAIL)} FALLIMENTI:\n" + "\n".join(FAIL)))
sys.exit(1 if FAIL else 0)
