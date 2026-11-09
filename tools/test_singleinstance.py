"""Verifica il lock a istanza singola (mutex tra processi diversi)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAIL = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]);"
    "from app import singleinstance as si;"
    "print(si.acquire())"
)


def child():
    r = subprocess.run([sys.executable, "-c", CHILD, str(ROOT)],
                       capture_output=True, text=True, cwd=str(ROOT))
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        print((r.stderr or "").strip())
        return None
    return out == "True"


print("1) prima istanza: acquire() -> True")
from app import singleinstance as si           # noqa: E402
check(si.acquire() is True, "il processo corrente acquisisce il mutex")

print("\n2) seconda istanza (processo separato) -> False")
check(child() is False, "il secondo processo viene respinto")

print("\n3) rilascio -> il secondo processo puo' acquisire")
si.release()
check(si.acquire() is True, "re-acquisito dal processo corrente")
si.release()
check(child() is True, "dopo il rilascio un nuovo processo passa")

print("\n4) focus_existing su finestra inesistente -> False")
check(si.focus_existing() is False,
      "nessuna finestra IACallMinute aperta (nessun effetto collaterale)")

print("\n" + ("TUTTI I TEST PASSATI" if not FAIL
              else f"{len(FAIL)} FALLIMENTI:\n" + "\n".join(FAIL)))
sys.exit(1 if FAIL else 0)
