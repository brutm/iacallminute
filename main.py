#!/usr/bin/env python3
"""IACallMinute — trascrizione live di call con AI.

Avvio GUI:      python main.py
Test automatici: python main.py --selftest [--no-ai]
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
import threading
import time
import traceback


def _redirect_stdio() -> None:
    """L'exe e' windowed: senza console sys.stdout/stderr sono None e ogni
    traceback (inclusi i messaggi fatali di Tcl) viene perso senza traccia.
    Reindirizza su logs/stderr.log."""
    try:
        from app import config
        if sys.stdout is not None and sys.stderr is not None:
            return
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LOG_DIR / "stderr.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            path.unlink()
        fh = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        if sys.stdout is None:
            sys.stdout = fh
        if sys.stderr is None:
            sys.stderr = fh
    except Exception:                           # noqa: BLE001
        pass


def _install_excepthook() -> None:
    """Cattura le eccezioni NON SOLO nel log (disattivato di default, quindi
    finisce in un NullHandler) ma anche su logs/crash.log, che viene scritto
    sempre, piu' le eccezioni non gestite nei thread."""
    log = logging.getLogger("iacm")

    def _dump(kind: str, text: str) -> None:
        try:
            from app import config
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.LOG_DIR / "crash.log", "a",
                      encoding="utf-8", errors="replace") as fh:
                fh.write(f"--- {kind} {time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"---\n{text.rstrip()}\n")
        except Exception:                       # noqa: BLE001
            pass

    def _hook(exc_type, exc, tb):
        log.critical("eccezione non gestita", exc_info=(exc_type, exc, tb))
        _dump("EXCEPTION", "".join(
            traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        _dump("THREAD EXCEPTION", "".join(
            traceback.format_exception(args.exc_type, args.exc_value,
                                       args.exc_traceback)))

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def _teardown_selftest() -> None:
    """I test GUI creano finestre Tk e thread: prima di uscire raccogli gli
    oggetti nel thread principale (Tk e' thread-confined: un GC che avviene
    in un thread secondario provoca "Tcl_AsyncDelete"). Eventuali thread
    residui vengono annotati su logs/selftest_teardown.txt per diagnosi."""
    import gc
    try:
        from app import config
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        for k in range(5):
            gc.collect()
            time.sleep(0.05)
        alive = [t.name for t in threading.enumerate()
                 if t is not threading.main_thread() and t.is_alive()]
        if alive:
            with open(config.LOG_DIR / "selftest_teardown.txt",
                      "a", encoding="utf-8") as fh:
                fh.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} — "
                    f"thread residui: {', '.join(alive)}\n")
    except Exception:                             # noqa: BLE001
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="IACallMinute",
        description="Trascrizione live Teams/Zoom con AI, traduzione, "
                    "riassunto e minute.")
    parser.add_argument("--selftest", action="store_true",
                        help="esegue i test automatici (scrive il report "
                             "sul file system)")
    parser.add_argument("--no-ai", action="store_true",
                        help="selftest senza chiamate di rete")
    args = parser.parse_args()

    from app import config
    _redirect_stdio()
    config.setup_logging()
    _install_excepthook()
    log = logging.getLogger("iacm")

    if args.selftest:
        log.info("selftest avviato (no_ai=%s)", args.no_ai)
        from app import selftest
        rc = selftest.run(with_ai=not args.no_ai)
        log.info("selftest concluso rc=%d", rc)
        _teardown_selftest()
        # Uscita immediata senza shutdown dell'interprete: i modelli
        # ctranslate2 (Whisper GPU/Argos) distrutti durante il teardown
        # provocano crash 0xC0000409 in modo intermittente.
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:                         # noqa: BLE001
            pass
        # TerminateProcess su se stessi: a differenza di ExitProcess
        # (os._exit) NON esegue i DLL_PROCESS_DETACH, dove a volte
        # ctranslate2/ucrtbase lancia una C++ exception e crasha con
        # 0xC0000409 in modo intermittente.
        import ctypes
        k32 = ctypes.windll.kernel32
        if not k32.TerminateProcess(k32.GetCurrentProcess(), rc):
            os._exit(rc)

    # una sola istanza alla volta: un secondo avvio porta in primo piano
    # la finestra gia' aperta ed esce senza creare una copia.
    from app import singleinstance
    if not singleinstance.ensure():
        return 0

    log.info("avvio GUI v%s", config.APP_VERSION)

    # prima di creare finestre: testo nitido con lo zoom di Windows
    from app.ui.tkutil import enable_dpi_awareness
    enable_dpi_awareness()

    # pulizia all'avvio: le sessioni temporanee non salvate esplicitamente
    # dall'utente (crash, chiusure improvvise, prove) spariscono qui, cosi'
    # non occupano disco indefinitamente
    try:
        config.purge_temp_sessions()
    except Exception:                             # noqa: BLE001
        log.exception("pulizia temporanee all'avvio fallita")

    # modelli locali: cartella accanto all'app + eventuale migrazione dalle
    # vecchie cache nascoste in home (nessun riscaricamento)
    try:
        from app.ai import local_models
        local_models.ensure_dirs()
        moved = local_models.migrate_models()
        if moved:
            log.info("modelli locali spostati in %s (%.0f MB)",
                     local_models.MODELS_DIR, moved / (1024 * 1024))
    except Exception:                             # noqa: BLE001
        log.exception("preparazione cartella modelli fallita")

    # Splash proprio: muovibile con il mouse e con barra di avanzamento.
    # Subito dopo la mappatura copre la finestra statica di PyInstaller,
    # poi resta visibile finche i moduli pesanti non sono caricati.
    from app import splash

    sp = splash.Splash()
    try:
        sp.show()
        splash.dismiss_pyi()

        sp.set("Caricamento moduli…", None)
        loader = threading.Thread(
            target=lambda: importlib.import_module("app.ui.window"),
            daemon=True, name="ui-load")
        loader.start()
        sp.spin("Caricamento moduli…", loader)
        loader.join()
        # nel thread principale: rende subito visibile un ImportError
        importlib.import_module("app.ui.window")

        sp.set("Interfaccia…", 85)
        app = sys.modules["app.ui.window"].App()
        sp.set("Pronto", 100)
        time.sleep(0.3)
        sp.close()
        app.mainloop()
        return 0
    finally:
        sp.close()


if __name__ == "__main__":
    sys.exit(main())
