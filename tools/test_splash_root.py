"""Regressione: lo splash NON deve rubare il root di default a Tk.

Se lo splash (tk.Tk) resta il default root, alla sua chiusura le StringVar
dell'App create senza master finiscono sull'interp dello splash → campi
vuoti e "Too early to create variable: no default root window" aprendo
Configurazione.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

from app import splash  # noqa: E402


def main() -> int:
    ok = 0

    sp = splash.Splash()
    sp.show()
    sp.set("test", 50)
    assert tk._default_root is not sp.root, "lo splash NON deve essere root di default"
    ok += 1
    print("1 splash non e' default root ok")

    from app.ui import window as win
    app = win.App()
    app.update_idletasks()
    assert tk._default_root is app, f"root attuale: {tk._default_root!r}"
    ok += 1
    print("2 App e' default root ok")

    # sequenza di main.py: splash chiuso PRIMA del mainloop
    sp.close()
    assert tk._default_root is app, "sp.close() ha azzerato il default root"
    ok += 1
    print("3 default root sopravvive a sp.close() ok")

    # le variabili dell'App devono stare sull'interp dell'App
    assert app.out_dir_var.__dict__.get("_root") is app, "var su root sbagliato"
    assert (app.out_dir_var.get() or "").strip(), "cartella destinazione vuota"
    assert (app.temp_dir_var.get() or "").strip(), "cartella temporanea vuota"
    ok += 1
    print("4 variabili valorizzate sull'App ok")

    # nuove variabili senza master (come nei dialog)
    v = tk.StringVar(value="x")
    assert v.__dict__.get("_root") is app, "nuova var su root sbagliato"
    v.set("y")
    assert v.get() == "y"
    ok += 1
    print("5 nuove StringVar ok")

    # lo status deve aggiornarsi dopo la chiusura dello splash
    app._set_status("prova post-splash")
    assert app.status_var.get() == "prova post-splash", app.status_var.get()
    ok += 1
    print("6 _set_status dopo sp.close() ok")

    # Configurazione: era qui che esplodeva il RuntimeError
    dlg = win.SettingsDialog(app)
    app.update_idletasks()
    dlg.destroy()
    ok += 1
    print("7 SettingsDialog apribile ok")

    app.destroy()
    ok += 1
    print("8 destroy pulita ok")

    print(f"PASS {ok}/8 test_splash_root")
    return 0


if __name__ == "__main__":
    sys.exit(main())
