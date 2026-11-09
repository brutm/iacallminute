"""Verifica lo splash screen: muovibile + barra di avanzamento."""
import sys
import threading
import time

sys.path.insert(0, ".")

from app import splash                                       # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


class E:
    pass


print("1) show(): finestra senza titlebar, centrata, visibile")
sp = splash.Splash()
sp.show()
check(sp.root is not None and sp.root.winfo_exists(), "finestra creata")
geo = sp.root.geometry()
check(geo.startswith(f"{splash.W}x{splash.H}"), f"geometria {geo}")
check(bool(sp.root.overrideredirect()), "nessuna titlebar (trascinabile ovunque)")

print("\n2) barra determinata (step di fase)")
sp.set("Interfaccia…", 85)
check(sp._bar["value"] == 85, f"value={sp._bar['value']} (atteso 85)")
check(str(sp._bar.cget("mode")) == "determinate", "mode=determinate")

print("\n3) barra indeterminata (durante il caricamento moduli)")
sp.set("Caricamento moduli…", None)
check(str(sp._bar.cget("mode")) == "indeterminate", "mode=indeterminate")
sp.root.update()
check(sp._status.get() == "Caricamento moduli…", "stato aggiornato")

print("\n4) trascinamento con il mouse")
root = sp.root
x0, y0 = root.winfo_rootx(), root.winfo_rooty()
e = E()
e.x_root, e.y_root = x0 + 60, y0 + 40
sp._on_press(e)
e.x_root, e.y_root = x0 + 360, y0 + 240
sp._on_move(e)
new_geo = root.geometry()


def _xy(g):
    x, y = g.rsplit("+", 2)[1:]
    return int(x), int(y)


dx = tuple(b - a for a, b in zip(_xy(geo), _xy(new_geo)))
check(dx == (300, 200),
      f"finestra spostata di {dx} (atteso (300, 200)): {geo} -> {new_geo}")

print("\n5) spin() pompa gli eventi mentre un thread lavora")
stop_at = []


def _worker():
    time.sleep(0.8)
    stop_at.append(time.monotonic())


t = threading.Thread(target=_worker, daemon=True)
t0 = time.monotonic()
t.start()
sp.spin("Caricamento moduli…", t)
t.join()
elapsed = time.monotonic() - t0
check(0.7 <= elapsed <= 3.0,
      f"ritorna con il thread finito ({elapsed:.2f}s, atteso ~0.8s)")
check(sp.root.winfo_exists(), "finestra viva dopo spin")

print("\n6) set() dopo close() e close() doppio non lanciano")
sp.close()
check(sp.root is None, "finestra distrutta")
sp.set("dopo la chiusura", 50)          # deve essere un no-op
sp.close()
check(True, "nessuna eccezione")

print("\n7) dismiss_pyi() senza pyi_splash (dev)")
splash.dismiss_pyi()
check(True, "nessuna eccezione in ambiente di sviluppo")

print("\n" + ("TUTTI I TEST PASSATI" if not FAIL
              else f"{len(FAIL)} FALLIMENTI:\n" + "\n".join(FAIL)))
sys.exit(1 if FAIL else 0)
