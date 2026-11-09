"""Schermata «attendi» con loader mostrata durante la chiusura (stop)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    import tkinter as tk
    from app.ui.window import App

    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "app", "ui", "window.py"),
        encoding="utf-8").read()
    assert "_show_stop_overlay()" in src.split(
        "def _on_stop")[1].split("def ")[0], "._on_stop non mostra l'overlay"
    assert "_hide_stop_overlay()" in src.split(
        "def _on_stopped")[1].split("def ")[0], "_on_stopped non lo nasconde"
    print("1 hook in _on_stop/_on_stopped ok")

    root = tk.Tk()
    root.withdraw()
    app = App()
    app.withdraw()
    try:
        # 1) show: creato e placeato su tutta la finestra
        app._show_stop_overlay()
        ovl = app._stop_ovl
        assert ovl is not None and ovl.winfo_exists(), "overlay non creato"
        root.update()
        info = ovl.place_info()
        assert info and str(info.get("relwidth")) in ("1", "1.0"), \
            f"place: {info}"
        assert ovl.winfo_manager() == "place", "overlay non in place"
        print("2 overlay creato e placeato ok")

        # 2) doppio show = nessun duplicato
        app._show_stop_overlay()
        assert app._stop_ovl is ovl, "doppio show ha duplicato l'overlay"
        print("3 show idempotente ok")

        # 3) update: il testo cambia
        app._update_stop_overlay("Attendi: 3 segmenti ancora in elaborazione")
        app.update_idletasks()
        msg = str(app._stop_ovl_msg.cget("text"))
        assert "3 segmenti" in msg, msg
        print("4 aggiornamento testo ok")

        # 4) hide: distrutto e resettato
        app._hide_stop_overlay()
        assert app._stop_ovl is None, "hide non ha azzerato il riferimento"
        alive = bool(ovl.winfo_exists())
        assert not alive, "widget overlay sopravvissuto alla destroy"
        print("5 hide ok")

        # 5) hide senza show = no-op
        app._hide_stop_overlay()
        print("6 hide senza show no-op ok")

        # 6) show → hide → show di nuovo (seconda sessione)
        app._show_stop_overlay()
        assert app._stop_ovl is not None
        app._hide_stop_overlay()
        print("7 riutilizzo ok")

        # 7) destroy pulita con overlay visibile (caso chiusura finestra
        #    mentre la sessione è in stopping)
        app._show_stop_overlay()
        app._destroy_after_stop = False
        app._cleanup_and_destroy()
        print("8 destroy con overlay ok")
    except Exception:
        try:
            app._destroy_after_stop = False
            app._cleanup_and_destroy()
        except Exception:
            pass
        raise
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

    print("PASS 8/8 test_stop_overlay")
    return 0


if __name__ == "__main__":
    sys.exit(main())
