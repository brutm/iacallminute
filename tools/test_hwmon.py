"""Barra di stato CPU/GPU/RAM: sampling nel main thread, nessun thread dedicato."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui import hwmon  # noqa: E402
from app.ui.hwmon import HwMon  # noqa: E402


def main() -> int:
    ok = 0

    # 1) il monitor produce subito una stringa ben formata (main thread)
    m = HwMon()
    m.start()
    s = m.snapshot()
    if hwmon.psutil is not None:
        assert s, "snapshot vuoto con psutil installato"
        assert re.match(r"^CPU \d+% (GPU \d+% )?RAM \d+[.,]\d+/\d+ GB$", s), s
    else:
        assert s == "", "senza psutil l'indicatore resta vuoto"
    ok += 1
    print(f"1 istantanea formattata ok: «{s}»")

    # 2) doppio start e start/stop ripetuti sono no-op indolori
    m.start()
    m.stop()
    m.stop_wait()
    m.join()
    assert m.snapshot() not in (None,), "snapshot deve restare una stringa"
    ok += 1
    print("2 start/stop no-op senza thread ok")

    # 3) nvml: util int >= 0 o None, mai eccezioni
    nv = hwmon._Nvml()
    u = nv.util()
    assert u is None or (isinstance(u, int) and 0 <= u <= 100), u
    ok += 1
    print(f"3 nvml util = {u} ok")

    # 4) nessun thread in background creato da HwMon
    import threading
    before = {t.name for t in threading.enumerate()}
    m2 = HwMon()
    m2.start()
    after = {t.name for t in threading.enumerate()}
    assert not (after - before), f"thread inaspettati: {after - before}"
    ok += 1
    print("4 nessun thread di campionamento ok")

    # 5) due istanze indipendenti non interferiscono
    a, b = m.snapshot(), m2.snapshot()
    assert isinstance(a, str) and isinstance(b, str), (a, b)
    ok += 1
    print("5 istanze multiple ok")

    print(f"PASS {ok}/5 test_hwmon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
