"""Verifica che Riassunto/Minute vengano in primo piano al PRIMO click."""
import sys, time
sys.path.insert(0, ".")

from app.ui.window import App

FAIL = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


app = App()
app.update_idletasks()
try:
    # caso tipico: principale a schermo intero
    try:
        app.state("zoomed")
    except Exception:
        app.geometry(f"{app.winfo_screenwidth()}x{app.winfo_screenheight()}+0+0")
    app.update_idletasks()
    print("stato principale:", app.state())

    print("\n1) prima apertura (viewer appena creato)")
    t0 = time.monotonic()
    app._open_viewer("summary")
    app.update_idletasks()
    dt = (time.monotonic() - t0) * 1000
    v = app.viewer
    check(v is not None and v.winfo_exists(), "viewer creato")
    check(v.winfo_ismapped(), "viewer mappato subito")
    check(v.title() == "Riassunto", f"titolo = {v.title()!r}")
    geo = v.geometry()
    x, y = geo.split("+")[1:]
    x, y = int(x), int(y)
    w = v.winfo_width()
    sw = v.winfo_screenwidth()
    print(f"     geometry={geo} w={w} screen={sw} in {dt:.0f} ms")
    check(x >= 0 and x + w <= sw + 2, f"interamente a schermo (x={x}, w={w}, sw={sw})")
    check(v.focus_displayof() is v, "focus sulla finestra viewer")

    print("\n2) ripasso differito (180 ms) non deve perdere il posto")
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)
    app.update_idletasks()
    geo2 = v.geometry()
    x2 = int(geo2.split("+")[1])
    check(x2 >= 0 and x2 + v.winfo_width() <= sw + 2,
          f"ancora a schermo ({geo2})")
    print(f"     focus dopo 500 ms: {v.focus_displayof()!r}")
    check(v.focus_displayof() is v, "focus mantenuto")

    print("\n3) seconda apertura (stessa istanza riusata)")
    app._open_viewer("minutes")
    app.update_idletasks()
    check(v.title() == "Minute", f"titolo = {v.title()!r}")
    check(v.focus_displayof() is v, "focus viewer dopo 2° click")

    print("\n4) l'utente sposta la principale -> il viewer la segue")
    app.state("normal")
    app.geometry("+120+80")
    app.update_idletasks()
    app.event_generate("<Configure>")
    app.update_idletasks()
    vx = int(v.geometry().split("+")[1])
    print(f"     principale x=120 -> viewer x={vx}")
    check(vx >= 120, "viewer affiancato alla principale")

    print("\n5) chiusura + riapertura (nuova istanza)")
    v.destroy()
    app.update_idletasks()
    app._open_viewer("summary")
    app.update_idletasks()
    check(app.viewer.winfo_exists(), "riaperto dopo destroy")
    check(app.viewer.focus_displayof() is app.viewer, "focus alla riapertura")

finally:
    app.destroy()

print("\n" + ("TUTTI I TEST PASSATI" if not FAIL
              else f"{len(FAIL)} FALLITI:\n" + "\n".join(FAIL)))
sys.exit(1 if FAIL else 0)
