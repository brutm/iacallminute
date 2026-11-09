"""Arresto automatico (timer): logica pura + integrazione con la finestra.

Nessun dispositivo audio reale: la sessione è simulata impostando lo stato
«running» e sostituendo _on_stop / il salvataggio in background.
"""
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import autostop  # noqa: E402


def test_pure() -> int:
    ok = 0
    assert autostop.parse_minutes("45") == 45
    assert autostop.parse_minutes(" 1 ") == 1
    assert autostop.parse_minutes("0") is None
    assert autostop.parse_minutes("abc") is None
    assert autostop.parse_minutes("1441") is None
    ok += 1
    for txt, exp in (("18:30", (18, 30)), ("9.05", (9, 5)),
                     ("18h30", (18, 30)), ("1830", (18, 30)),
                     ("24:00", None), ("12:60", None), ("", None)):
        assert autostop.parse_clock(txt) == exp, (txt, exp)
    ok += 1
    now = dt.datetime(2026, 1, 1, 17, 0, 0)
    assert autostop.seconds_until(18, 0, now) == 3600
    assert autostop.seconds_until(16, 0, now) == 23 * 3600  # domani
    assert autostop.seconds_until(17, 0, now) == 24 * 3600
    ok += 1
    d = autostop.compute_deadline("after", "10", start_mono=100.0,
                                  now_mono=400.0)
    assert d == 700.0, d
    d = autostop.compute_deadline("at", "17:30", start_mono=0.0,
                                  now_mono=50.0, now_dt=now)
    assert d == 50.0 + 1800, d
    assert autostop.compute_deadline("off", "10", start_mono=0,
                                     now_mono=0) is None
    assert autostop.compute_deadline("after", "x", start_mono=0,
                                     now_mono=0) is None
    ok += 1
    assert autostop.format_remaining(59.2) == "01:00"
    assert autostop.format_remaining(3725) == "1:02:05"
    assert "1 h 30 min" in autostop.describe("after", "90")
    assert "18:05" in autostop.describe("at", "18.05")
    ok += 1
    print("logica pura ok (%d)" % ok)
    return ok


def test_gui() -> int:
    import tkinter as tk
    from app import config
    from app.ui.window import App

    config.save_settings = lambda: None     # non tocca settings.json
    ok = 0
    root = tk.Tk()
    root.withdraw()
    app = App()
    app.withdraw()
    try:
        # 1) default: timer spento, nessun campo valore visibile
        assert app._autostop_mode() == "off"
        assert not app.autostop_min_sb.winfo_manager()
        ok += 1

        # 2) modalità «dopo» mostra lo spinbox e le opzioni di salvataggio
        assert not app.autosave_row.winfo_manager()
        app.autostop_mode_var.set(autostop.MODE_LABELS["after"])
        app._on_autostop_change()
        assert app.autostop_min_sb.winfo_manager() == "pack"
        assert not app.autostop_at_entry.winfo_manager()
        assert app.autosave_row.winfo_manager() == "grid"
        ok += 1

        # 3) sessione simulata: scadenza armata e conteggio alla rovescia
        stops = []
        app._on_stop = lambda: stops.append(time.monotonic())
        app.state_ = "running"
        app.started_mono = time.monotonic()
        app.autostop_min_var.set("1")
        app._arm_autostop()
        assert app._autostop_deadline is not None
        assert abs(app._autostop_deadline - app.started_mono - 60) < 0.01
        app._tick_autostop(time.monotonic())
        assert "stop tra" in app.timer_var.get(), app.timer_var.get()
        assert app._autostop_warned        # dentro l'ultimo minuto
        ok += 1

        # 4) scadenza -> _on_stop chiamato una sola volta, flag impostato
        app._tick_autostop(app._autostop_deadline + 0.1)
        app._tick_autostop(time.monotonic() + 999)
        assert len(stops) == 1, stops
        assert app._autostop_fired
        assert app._autostop_deadline is None
        ok += 1

        # 5) modifica a sessione in corso con limite già superato -> grazia
        app._autostop_fired = False
        app.started_mono = time.monotonic() - 600   # 10 min fa
        app.autostop_min_var.set("5")
        app._on_autostop_change()
        rem = app._autostop_deadline - time.monotonic()
        assert 50 < rem <= autostop.GRACE_S, rem
        ok += 1

        # 6) disattivazione a sessione in corso
        app.autostop_mode_var.set(autostop.MODE_LABELS["off"])
        app._on_autostop_change()
        assert app._autostop_deadline is None
        ok += 1

        # 6b) fine chiamata: la casella mostra le opzioni di salvataggio
        assert not app.autosave_row.winfo_manager()
        app.callend_var.set(True)
        app._on_callend_toggle()           # running -> watcher armato
        assert app.autosave_row.winfo_manager() == "grid"
        assert app._callwatch is not None
        ok += 1

        # 6c) Teams col microfono aperto, poi chiuso -> stop dopo conferma
        from app import callwatch

        class FakeProbe:
            checked = True
            latest = {"Teams"}

            def stop(self):
                pass

        app._micprobe.stop()               # sostituisce la sonda reale
        probe = FakeProbe()
        app._micprobe = probe
        t0 = time.monotonic()
        app._autostop_fired = False
        stops.clear()
        app._tick_callwatch(t0)
        assert app._callwatch.seen_call
        probe.latest = set()
        app._tick_callwatch(t0 + 5)
        assert "stop tra" in app.timer_var.get(), app.timer_var.get()
        app._tick_callwatch(t0 + 10)
        assert not stops
        app._tick_callwatch(t0 + 5 + callwatch.CONFIRM_S + 1)
        assert len(stops) == 1, stops
        assert app._autostop_fired
        assert app._autostop_reason == "fine chiamata"
        assert app._callwatch is None and app._micprobe is None
        app.callend_var.set(False)
        app._on_callend_toggle()
        assert not app.autosave_row.winfo_manager()
        app._autostop_reason = "timer"
        ok += 1

        # 7) salvataggio automatico al termine: audio (senza video)
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp(prefix="selftest_autostop_"))
        wav = tmp / "recording.wav"
        import wave
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        app._wav_temp = wav
        app._video_temp = None
        app.out_dir_var.set(str(tmp / "out"))
        app.results = [{"start": 0.0, "italian": "ciao", "original": "",
                        "language": "it", "error": None}]
        started = []
        app._start_save = lambda title, msg, work: started.append(
            (title, work))
        app.state_ = "stopped"
        assert app._autosave_after_timer()
        title, work = started[0]
        assert "registrazione" in title, title
        app._save_cancel = None
        res = work()
        files = sorted(p.name for p in (tmp / "out").iterdir())
        assert any(f.startswith("registrazione_") for f in files), files
        assert any(f.startswith("trascrizione_") for f in files), files
        assert "Timer" in res["title"], res
        ok += 1

        # 8) con video presente sceglie il video
        vid = tmp / "schermo_raw.mp4"
        vid.write_bytes(b"x" * 2000)
        app._video_temp = str(vid)
        started.clear()
        assert app._autosave_after_timer()
        assert "video" in started[0][0], started[0][0]
        ok += 1

        # 9) niente registrazione -> nessun salvataggio
        app._video_temp = None
        app._wav_temp = None
        started.clear()
        assert not app._autosave_after_timer()
        assert not started
        ok += 1

        # 10) riassunto + minute generati e salvati (AI simulata)
        from app.ai import service
        orig_complete = service.complete
        orig_missing = config.missing_credentials
        service.complete = lambda s, system, user, on_status=None: (
            "# Documento\n\nContenuto generato per il test.")
        config.missing_credentials = lambda settings=None: []
        try:
            app._wav_temp = wav
            app.autosave_tx_var.set(False)
            app.autosave_sum_var.set(True)
            app.autosave_min_var.set(True)
            out2 = tmp / "out2"
            app.out_dir_var.set(str(out2))
            started.clear()
            assert app._autosave_after_timer()
            res = started[0][1]()
            files = sorted(p.name for p in out2.iterdir())
            assert any(f.startswith("riassunto_") for f in files), files
            assert any(f.startswith("minute_") and f.endswith(".md")
                       for f in files), files
            assert any(f.endswith(".docx") for f in files), files
            assert not any(f.startswith("trascrizione_")
                           for f in files), files
            assert res["kind"] == "info", res
            ok += 1

            # 11) credenziali mancanti: audio salvato, avviso, niente doc
            config.missing_credentials = lambda settings=None: [
                "testo: manca la chiave"]
            out3 = tmp / "out3"
            app.out_dir_var.set(str(out3))
            started.clear()
            assert app._autosave_after_timer()
            res = started[0][1]()
            files = sorted(p.name for p in out3.iterdir())
            assert files and all(f.startswith("registrazione_")
                                 for f in files), files
            assert res["kind"] == "warning" and "chiave" in res["msg"], res
            ok += 1
        finally:
            service.complete = orig_complete
            config.missing_credentials = orig_missing
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        app.state_ = "idle"
        app._destroy_after_stop = False
        app._cleanup_and_destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass
    print("integrazione finestra ok (%d)" % ok)
    return ok


def test_callwatch() -> int:
    from app import callwatch as cw
    ok = 0
    assert cw.call_app_name(
        r"C:\Program Files\WindowsApps\MSTeams_x64__8wekyb3d8bbwe"
        r"\ms-teams.exe") == "Teams"
    assert cw.call_app_name(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe") == "Chrome"
    assert cw.call_app_name(r"C:\Python\python.exe") is None
    ok += 1
    # chiamata vista, rilasciata, ripresa, poi finita davvero
    w = cw.CallWatch(confirm_s=30, silence_s=300)
    assert w.poll(set(), 0) is None
    assert w.poll({"Teams"}, 3) == "call"
    assert w.poll({"Teams"}, 6) is None
    assert w.poll(set(), 9) == "gone"
    assert w.remaining(19) == 20
    assert w.poll({"Teams"}, 20) == "back"
    assert w.poll(set(), 30) == "gone"
    assert w.poll(set(), 50) is None
    assert w.poll(set(), 60) == "end"
    assert w.poll(set(), 99) is None          # una volta sola
    ok += 1
    # nessuna app: stop solo dopo aver sentito audio e poi silenzio
    w = cw.CallWatch(confirm_s=30, silence_s=300)
    assert w.poll(None, 1000) is None         # mai sentito audio
    w.sound(1000)
    assert w.poll(None, 1200) is None
    w.sound(1250)
    assert w.poll(None, 1500) is None
    assert w.poll(None, 1550) == "silence"
    ok += 1
    # stato ignoto a chiamata in corso: nessuna decisione
    w = cw.CallWatch(confirm_s=30, silence_s=300)
    assert w.poll({"Teams"}, 0) == "call"
    assert w.poll(None, 10) is None and w.gone_since is None
    ok += 1
    # con chiamata vista il silenzio non ferma
    w = cw.CallWatch(confirm_s=30, silence_s=10)
    w.sound(0)
    assert w.poll({"Zoom"}, 1) == "call"
    assert w.poll({"Zoom"}, 100) is None
    ok += 1
    # app ancora col microfono ma audio assente da idle_s: fine (Webex)
    w = cw.CallWatch(confirm_s=30, idle_s=60)
    w.sound(0)
    assert w.poll({"Webex"}, 1) == "call"
    assert w.poll({"Webex"}, 30) is None           # audio silenzioso ma <60
    assert w.poll({"Webex"}, 59) is None
    assert w.poll({"Webex"}, 61) == "idle"         # 61-0 >= 60
    assert w.poll({"Webex"}, 62) is None           # una volta sola
    ok += 1
    # durante l'idle l'audio di nuovo presente ripristina il conto
    w = cw.CallWatch(confirm_s=30, idle_s=60)
    w.sound(0)
    assert w.poll({"Webex"}, 1) == "call"
    assert w.poll({"Webex"}, 50) is None
    w.sound(50)                                    # qualcuno torna a parlare
    assert w.poll({"Webex"}, 105) is None          # 55 s dal suono, no
    assert w.poll({"Webex"}, 111) == "idle"        # 61 s dal suono
    ok += 1    # mai suono: l'idle non deve fermare (call da cominciare)
    w = cw.CallWatch(confirm_s=30, idle_s=60)
    assert w.poll({"Webex"}, 1) == "call"
    assert w.poll({"Webex"}, 999) is None
    ok += 1
    # conto all'indietro idle mostrato solo negli ultimi SHOW_IDLE_FOR
    g = cw.CallWatch(confirm_s=30, silence_s=1, idle_s=300)
    g.sound(0)
    assert g.poll({"Webex"}, 1) == "call"
    assert g.remaining(100) is None                # show solo negli ultimi 120 s
    assert g.remaining(200) == 100
    w = cw.CallWatch(confirm_s=30, idle_s=60)      # idle breve: si mostra subito
    w.sound(0)
    assert w.poll({"Webex"}, 1) == "call"
    assert w.remaining(20) == 40
    assert w.poll(set(), 35) == "gone"             # mic rilasciato: vince gone
    assert w.remaining(45) == 20                   # conferma gone
    ok += 1
    # sessioni audio reali: insieme (eventualmente vuoto) su Windows; la
    # sonda in thread produce un risultato
    apps = cw.mic_apps_in_use()
    assert apps is None or isinstance(apps, set)
    probe = cw.MicProbe(interval=0.2)
    for _ in range(50):
        if probe.checked:
            break
        time.sleep(0.05)
    probe.stop()
    assert probe.checked
    ok += 1
    print("fine chiamata ok (%d)" % ok)
    return ok


def main() -> int:
    n = test_pure() + test_callwatch() + test_gui()
    print("PASS %d/28 test_autostop" % n)
    return 0 if n == 28 else 1


if __name__ == "__main__":
    sys.exit(main())
