"""Test automatici end-to-end: python main.py --selftest [--no-ai]

Scrive anche il report su file così è verificabile anche dall'eseguibile
senza console. Il report finisce in %APPDATA%\\IACallMinute\\logs.
"""
from __future__ import annotations

import base64
import subprocess
import sys

from . import subproc
import tempfile
import threading
import time
import traceback
from pathlib import Path

RESULTS: list[tuple[str, str, str]] = []


def _env_fail_msg(exc: BaseException) -> str | None:
    """Quota giornaliera esaurita o picco Google: non e' un fallimento
    del codice -> il test puo' andare in SKIP (si riprova piu' tardi)."""
    m = str(exc)
    if ("quota raggiunta" in m or "in pausa per quota" in m
            or "high demand" in m or "retry in" in m):
        return m.splitlines()[0][:120]
    return None


def _rm_file(p: Path, tentativi: int = 8) -> None:
    """unlink robusto su Windows: un file puo' restare bloccato per qualche
    istante da un handle residuo (thread di cattura, antivirus, indexer)."""
    for k in range(tentativi):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.25 * (k + 1))
    # ultimo tentativo: rinomina cosi' la creazione del file non fallisce
    try:
        p.replace(p.with_suffix(p.suffix + f".stale{time.time_ns()}"))
    except Exception:                             # noqa: BLE001
        pass


def _report(name: str, status: str, detail: str = "") -> bool:
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return status != "FAIL"


def _ps(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subproc.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)


def _make_voice_wav(path: Path) -> bool:
    """Sintetizza una voce inglese (fallback: voce di default)."""
    text = ("Hello everyone, welcome to the weekly meeting. Today we discuss "
            "the backend migration and the new payment module.")
    script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
  $en = $s.GetInstalledVoices() |
    Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'en*' }} |
    Select-Object -First 1
  if ($en) {{ $s.SelectVoice($en.VoiceInfo.Name) }}
}} catch {{ }}
$s.SetOutputToWaveFile('{path}')
$s.Speak('{text}')
$s.Dispose()
Write-Output 'done'
"""
    try:
        _ps(script)
        return path.is_file() and path.stat().st_size > 1000
    except Exception:
        return False


def _gui_modes_test() -> str:
    """Con video: temporaneo + «Salva video…» (mp4+audio) + pulizia; senza: no."""
    from app.ui import window as win_mod
    from app.ui.window import App

    msgs: list[str] = []
    mb = win_mod.messagebox
    fd = win_mod.filedialog
    orig_mb = (mb.showinfo, mb.showerror, mb.showwarning, mb.askyesno)
    orig_ask = fd.asksaveasfilename
    mb.showinfo = lambda *a, **k: msgs.append(f"info: {a[0] if a else ''}")
    mb.showerror = lambda *a, **k: msgs.append(f"error: {a[0] if a else ''}")
    mb.showwarning = lambda *a, **k: msgs.append(f"warning: {a[0] if a else ''}")
    mb.askyesno = lambda *a, **k: True

    out_dir = Path(tempfile.mkdtemp(prefix="iacm_vid_"))
    saved_out = out_dir / "video_salvato.mp4"
    fd.asksaveasfilename = lambda **k: str(saved_out)

    from app import config as _cfg
    _old_video = _cfg.SETTINGS.get("record_video", False)
    app: App | None = App()
    app.withdraw()
    try:
        def _pump(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.update()
                time.sleep(0.02)

        def _wait(target: str, timeout: float) -> None:
            end = time.monotonic() + timeout
            while app.state_ != target and time.monotonic() < end:
                app.update()
                time.sleep(0.02)
            if app.state_ != target:
                raise RuntimeError(f"stato {target!r} non raggiunto "
                                   f"(attuale {app.state_!r})")

        captured: dict = {}
        orig_stopped = app._on_stopped
        app._on_stopped = lambda info: (captured.update(info),
                                        orig_stopped(info))

        def _session(video: bool, pump_s: float) -> None:
            captured.clear()
            app.video_var.set(video)
            app._on_start()
            if app.state_ != "running":
                raise RuntimeError("avvio non partito")
            _pump(pump_s)
            app._on_stop()
            _wait("stopped", 90)

        # 1) senza video: niente file video
        _session(False, 1.2)
        if app._has_video() or captured.get("video_temp"):
            raise RuntimeError("video presente senza averlo chiesto")

        # 2) con video: temporaneo presente + «Salva video…» con audio
        _session(True, 2.5)
        if not app._has_video():
            raise RuntimeError(
                f"video temporaneo mancante: warn={captured.get('warnings')}")
        temp_vid = app._video_temp
        temp_wav = app._wav_temp
        app._on_save_video()
        # il salvataggio gira in un thread con finestra di progresso: si
        # pompa l'event loop finite che il dialogo si chiude (o timeout)
        end = time.monotonic() + 180
        while time.monotonic() < end:
            app.update()
            if not (getattr(app, "_save_dlg", None)
                    and app._save_dlg.winfo_exists()):
                break
            time.sleep(0.02)
        if getattr(app, "_save_dlg", None) is not None:
            raise RuntimeError("la finestra di salvataggio non si e' "
                               "chiusa (worker ancora vivo)")
        if not saved_out.is_file() or saved_out.stat().st_size < 1000:
            raise RuntimeError("«Salva video…» non ha prodotto il file")
        import subprocess
        r = subproc.run(["ffmpeg", "-hide_banner", "-i", str(saved_out)],
                           capture_output=True, text=True)
        if not ("Video:" in r.stderr and "Audio:" in r.stderr):
            raise RuntimeError("mp4 salvato incompleto (video/audio)")

        # 3) chiusura: i temporanei (wav + video) spariscono
        app._destroy_after_stop = False
        app._cleanup_and_destroy()
        app = None
        leftovers = [q for q in (temp_vid, temp_wav)
                     if q and Path(q).exists()]
        if leftovers:
            raise RuntimeError(f"temporanei non cancellati: {leftovers}")

        sz = saved_out.stat().st_size
        return (f"senza-video ok · video salvato {sz}b (con audio) · "
                f"temporanei cancellati alla chiusura")
    finally:
        if app is not None:
            try:
                app._destroy_after_stop = False
                app._cleanup_and_destroy()
            except Exception:
                pass
        (mb.showinfo, mb.showerror, mb.showwarning, mb.askyesno) = orig_mb
        fd.asksaveasfilename = orig_ask
        # la casella «Con video» non si persiste piu' (vale per la sessione)
        _cfg.SETTINGS["record_video"] = False
        try:
            saved_out.unlink(missing_ok=True)
            out_dir.rmdir()
        except OSError:
            pass


def _write_report() -> Path:
    try:
        from app import config
        out = config.LOG_DIR
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        out = Path(tempfile.gettempdir()) / "IACallMinute"
        out.mkdir(parents=True, exist_ok=True)
    report = out / "selftest_report.txt"
    lines = [f"{status:5} {name}" + (f" — {d}" if d else "")
             for name, status, d in RESULTS]
    fails = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    lines.append("")
    lines.append("RISULTATO: " + ("FAIL" if fails else "OK")
                 + f" ({fails} falliti, {len(RESULTS)} totali)")
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def _gui_restart_test() -> str:
    """Avvio → Ferma → riavvio (regressione del bug «FERMA poi AVVIA»)."""
    from app.ui import window as win_mod
    from app.ui.window import App

    msgs: list[str] = []
    mb = win_mod.messagebox
    orig = (mb.showinfo, mb.showerror, mb.showwarning, mb.askyesno)
    mb.showinfo = lambda *a, **k: msgs.append(f"info: {a[0] if a else ''}")
    mb.showerror = lambda *a, **k: msgs.append(f"error: {a[0] if a else ''}")
    mb.showwarning = lambda *a, **k: msgs.append(f"warning: {a[0] if a else ''}")
    mb.askyesno = lambda *a, **k: True

    app = App()
    app.withdraw()
    try:
        def _pump(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.update()
                time.sleep(0.02)

        def _wait(target: str, timeout: float) -> None:
            end = time.monotonic() + timeout
            while app.state_ != target and time.monotonic() < end:
                app.update()
                time.sleep(0.02)
            if app.state_ != target:
                raise RuntimeError(
                    f"stato {target!r} non raggiunto "
                    f"(attuale {app.state_!r}) — dialoghi: {msgs}")

        app._on_start()
        if app.state_ != "running":
            raise RuntimeError(
                f"primo avvio non partito (stato {app.state_!r}) "
                f"— dialoghi: {msgs}")
        _pump(1.2)
        app._on_stop()
        _wait("stopped", 60)

        app._on_start()          # qui era il bug: restava silenziosamente idle
        if app.state_ != "running":
            raise RuntimeError(
                f"riavvio dopo Ferma non partito (stato {app.state_!r}) "
                f"— dialoghi: {msgs}")
        _pump(1.0)
        app._on_stop()
        _wait("stopped", 60)
        return "avvio → stop → riavvio → stop ok" + (
            f" · dialoghi: {msgs}" if msgs else "")
    finally:
        try:
            app._destroy_after_stop = False
            app._cleanup_and_destroy()
        except Exception:
            pass
        (mb.showinfo, mb.showerror, mb.showwarning, mb.askyesno) = orig


def run(with_ai: bool = True) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    tmp = Path(tempfile.gettempdir()) / "IACallMinute"
    tmp.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1. import ---------------------------------------------------------
    try:
        from app import config
        from app.ai import context, service
        from app.audio import capture, segmenter  # noqa: F401
        from app.storage import outputs, recorder
        from app.transcription import pipeline  # noqa: F401
        _report("import moduli", "OK")
    except Exception:
        _report("import moduli", "FAIL", traceback.format_exc(limit=1))
        _write_report()
        return 1

    # 2. credenziali provider -------------------------------------------
    missing = config.missing_credentials()
    if not with_ai:
        _report("credenziali provider", "SKIP", "--no-ai")
        creds_ok = False
    elif missing:
        _report("credenziali provider", "FAIL", "; ".join(missing))
        creds_ok = False
    else:
        creds_ok = True
        stt, txt = config.SETTINGS["stt"], config.SETTINGS["text"]
        _report("credenziali provider", "OK",
                f"stt={stt['type']}:{stt.get('model')} · "
                f"testo={txt['type']}:{txt.get('model')}")

    # 3. ffmpeg ---------------------------------------------------------
    ff = recorder.ffmpeg_path()
    if ff:
        _report("ffmpeg (MP3)", "OK", ff)
    else:
        _report("ffmpeg (MP3)", "OK",
                "assente — fallback WAV con avviso (installa ffmpeg)")

    # 4. dispositivi audio ----------------------------------------------
    loopback = None
    try:
        devs = capture.list_audio_devices()
        loopback = devs["default_loopback"]
        _report("dispositivi audio", "OK",
                f"{len(devs['loopbacks'])} loopback, {len(devs['mics'])} mic"
                + (f", default: {loopback['name']}" if loopback else ""))
        if loopback is None:
            _report("loopback default", "FAIL", "nessun dispositivo loopback")
    except Exception as exc:
        _report("dispositivi audio", "FAIL", str(exc))

    # 5. AI: testo (provider configurato) --------------------------------
    if not with_ai or not creds_ok:
        _report("AI testo", "SKIP", "--no-ai" if not with_ai else "credenziali")
    else:
        try:
            txt = service.complete(
                config.SETTINGS,
                "Rispondi in italiano in una parola.",
                "Scrivi: funziona")
            if "funziona" in txt.lower():
                _report("AI testo", "OK", txt.strip()[:40])
            else:
                _report("AI testo", "FAIL", f"risposta inattesa: {txt[:80]}")
        except Exception as exc:
            _env = _env_fail_msg(exc)
            _report("AI testo", "SKIP" if _env else "FAIL",
                    _env or str(exc))

    # 6. voce sintetica --------------------------------------------------
    voice_wav = tmp / "selftest_voice.wav"
    if voice_wav.exists():
        _rm_file(voice_wav)
    has_voice = _make_voice_wav(voice_wav)
    if has_voice:
        _report("sintesi voce (SAPI)", "OK", f"{voice_wav.stat().st_size} byte")
    else:
        _report("sintesi voce (SAPI)", "FAIL", "wav non generato")

    # 7. AI audio (trascrizione diretta) ---------------------------------
    if not with_ai or not creds_ok or not has_voice:
        _report("AI trascrizione audio", "SKIP")
    else:
        try:
            wav_bytes = voice_wav.read_bytes()
            r = service.transcribe_segment(
                config.SETTINGS, wav_bytes,
                duration=6.0, start=0.0, bilingual=False)
            if (r.get("italian") or "").strip():
                _report("AI trascrizione audio", "OK",
                        f"lingua={r['language']}: {r['italian'][:70]}")
            else:
                _report("AI trascrizione audio", "FAIL",
                        f"trascrizione vuota: {r}")
        except Exception as exc:
            _env = _env_fail_msg(exc)
            _report("AI trascrizione audio", "SKIP" if _env else "FAIL",
                    _env or str(exc))

    # 8. cattura live + VAD + pipeline (E2E) ------------------------------
    segments: list[tuple[float, object]] = []
    errors: list[str] = []
    if loopback is None:
        _report("cattura live + VAD", "FAIL", "nessun loopback")
    else:
        import winsound
        from app.audio.capture import AudioSession

        wav_out = tmp / "selftest_capture.wav"
        if wav_out.exists():
            _rm_file(wav_out)
        session = AudioSession(
            loopback, None, wav_out,
            on_segment=lambda s, p: segments.append((s, p)),
            on_error=errors.append, on_status=lambda m: None)
        try:
            session.start()
        except Exception as exc:
            _report("cattura live + VAD", "FAIL", f"avvio: {exc}")
            session = None

        if session is not None:
            if has_voice:
                def _play() -> None:
                    try:
                        winsound.PlaySound(str(voice_wav),
                                           winsound.SND_FILENAME)
                    except Exception as exc:
                        errors.append(f"playback: {exc}")
                t_play = time.monotonic()
                play = threading.Thread(target=_play, daemon=True)
                play.start()
                play.join(timeout=30)
                if time.monotonic() - t_play < 1.0:  # playback fallito subito
                    time.sleep(5)
            else:
                time.sleep(5)
            time.sleep(1.8)  # silenzio per chiudere l'ultimo segmento
            stats = session.stop()
            ok_wav = wav_out.is_file() and wav_out.stat().st_size > 0
            detail = (f"durata={stats['duration']:.1f}s, "
                      f"segmenti={len(segments)}, "
                      f"wav={'si' if ok_wav else 'no'}")
            if errors:
                detail += f", errori={errors[:2]}"
            if segments and ok_wav:
                _report("cattura live + VAD", "OK", detail)
            else:
                _report("cattura live + VAD", "FAIL", detail)

            # 8b. trascrizione del segmento catturato live
            if with_ai and creds_ok and segments:
                try:
                    from app.ai import gemini as g2
                    pcm = segments[0][1]
                    wb = g2.build_wav_bytes(pcm.tobytes())
                    r = service.transcribe_segment(
                        config.SETTINGS, wb,
                        duration=pcm.size / config.TARGET_RATE,
                        start=segments[0][0], bilingual=False)
                    if (r.get("italian") or "").strip():
                        _report("E2E live -> AI", "OK",
                                f"lingua={r['language']}: "
                                f"{r['italian'][:70]}")
                    else:
                        _report("E2E live -> AI", "FAIL",
                                "trascrizione vuota dal segmento live")
                except Exception as exc:
                    _env = _env_fail_msg(exc)
                    _report("E2E live -> AI", "SKIP" if _env else "FAIL",
                            _env or str(exc))
            elif not with_ai:
                _report("E2E live -> AI", "SKIP", "--no-ai")

    # 9. output md + docx -------------------------------------------------
    try:
        fake = [{
            "start": 3.0, "duration": 4.0, "language": "en",
            "italian": "Ciao a tutti, benvenuti alla riunione.",
            "original": "Hello everyone, welcome to the meeting.",
            "bilingual": True, "error": None,
        }]
        md = outputs.build_transcript_md(fake, 4.0)
        tx = outputs.write_text(tmp / "selftest_transcript.md", md)
        docx_path = outputs.markdown_to_docx(
            "# Minute di prova\n\n## Decisioni\n- uso IACallMinute\n",
            tmp / "selftest_minute.docx")
        if tx.is_file() and docx_path.is_file() and docx_path.stat().st_size > 0:
            _report("output md + docx", "OK",
                    f"{docx_path.stat().st_size} byte docx")
        else:
            _report("output md + docx", "FAIL", "file mancanti")
    except Exception as exc:
        _report("output md + docx", "FAIL", str(exc))

    # 10. analisi contesto progetto ---------------------------------------
    try:
        proj = Path(__file__).resolve().parent.parent  # root progetto
        c = context.build_project_context(proj, "test contesto")
        if len(c) > 100:
            _report("analisi contesto progetto", "OK",
                    f"{len(c)} caratteri da {proj.name}")
        else:
            _report("analisi contesto progetto", "FAIL", "digest vuoto")
    except Exception as exc:
        _report("analisi contesto progetto", "FAIL", str(exc))

    # 11. settings + purge temporanee -------------------------------------
    try:
        fake = config.get_temp_root() / "session_selftest_fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "x.tmp").write_text("x", encoding="utf-8")
        n = config.purge_temp_sessions()
        if not fake.exists() and n >= 1:
            _report("settings + purge temporanee", "OK",
                    f"rimosse {n} voci · settings="
                    f"{'si' if config.SETTINGS_FILE.is_file() else 'no'}")
        else:
            _report("settings + purge temporanee", "FAIL",
                    f"cartella ancora presente o rimosse={n}")
    except Exception as exc:
        _report("settings + purge temporanee", "FAIL", str(exc))

    # 12. registrazione solo MP3 -------------------------------------------
    try:
        import wave
        d = tmp / "session_recmp"
        d.mkdir(parents=True, exist_ok=True)
        wpath = d / "recording.wav"
        with wave.open(str(wpath), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.TARGET_RATE)
            w.writeframes(b"\x00\x00" * config.TARGET_RATE)  # 1s silenzio
        dest = tmp / "registrazione_TESTREC.mp3"
        dest.unlink(missing_ok=True)
        rec = recorder.save_recording(wpath, dest)
        saved = rec.get("path")
        temp_kept = wpath.exists()
        if saved and Path(saved).is_file() and temp_kept:
            _report("salva registrazione su richiesta", "OK",
                    f"{Path(saved).stat().st_size} byte · temporaneo "
                    "conservato per altri salvataggi")
        else:
            _report("salva registrazione su richiesta", "FAIL",
                    f"saved={saved} temp_kept={temp_kept} "
                    f"warnings={rec.get('warnings')}")
    except Exception as exc:
        _report("registrazione solo MP3", "FAIL", str(exc))

    # 13. GUI: avvio → ferma → riavvio (regressione bug restart) ----------
    if loopback is None:
        _report("GUI riavvio dopo Ferma", "SKIP", "nessun loopback")
    elif missing:
        _report("GUI riavvio dopo Ferma", "SKIP", "credenziali mancanti")
    else:
        try:
            detail = _gui_restart_test()
            _report("GUI riavvio dopo Ferma", "OK", detail)
        except Exception as exc:
            _report("GUI riavvio dopo Ferma", "FAIL", str(exc))

    # 14. registrazione schermo (cattura + codifica diretta MP4) -----------
    try:
        from .storage import screenrec as srec
        out = tmp / "selftest_schermo.mp4"
        out.unlink(missing_ok=True)
        srec_re = srec.ScreenRecorder((64, 64, 320, 180), 10, out)
        srec_re.start()
        time.sleep(1.5)   # l'exe onefile e' lento ad avviare mss+ffmpeg
        saved = srec_re.stop()
        if saved and saved.is_file() and saved.stat().st_size > 500:
            _report("registrazione schermo", "OK",
                    f"{saved.stat().st_size} byte - {srec_re.frames} frame")
        else:
            _report("registrazione schermo", "FAIL",
                    f"saved={saved} err={srec_re.error}")
    except ModuleNotFoundError as exc:
        _report("registrazione schermo", "SKIP", f"dipendenza: {exc}")
    except Exception as exc:
        _report("registrazione schermo", "FAIL", str(exc))

    # 15. modalita': video MP4+audio e audio MP3 (salvataggio auto) --------
    if loopback is None:
        _report("modalita' video/audio", "SKIP", "nessun loopback")
    elif missing:
        _report("modalita' video/audio", "SKIP", "credenziali mancanti")
    else:
        try:
            detail = _gui_modes_test()
            _report("modalita' video/audio", "OK", detail)
        except Exception as exc:
            _report("modalita' video/audio", "FAIL", str(exc))

    # 16. analisi video: estrazione frame + AI ------------------------------
    try:
        import shutil as _sh
        from .storage import screenrec as srec3
        an_v = tmp / "selftest_anvideo.mp4"
        an_v.unlink(missing_ok=True)
        rec3 = srec3.ScreenRecorder((80, 80, 640, 360), 10, an_v)
        rec3.start()
        time.sleep(1.5)
        vs = rec3.stop()
        if not vs:
            raise RuntimeError(f"video di test non salvato: {rec3.error}")
        fr = srec3.extract_frames(vs, tmp / "anframes", count=4)
        if len(fr) < 2:
            raise RuntimeError(f"solo {len(fr)} frame estratti")
        if config.SETTINGS.get("text", {}).get("type") != "gemini":
            _report("analisi video (frame + AI)", "SKIP",
                    f"{len(fr)} frame ok (richiede provider testo Gemini)")
        elif missing:
            _report("analisi video (frame + AI)", "SKIP",
                    f"{len(fr)} frame ok (credenziali mancanti)")
        else:
            from .ai import service as svc2
            txt = svc2.analyze_video(config.SETTINGS, vs,
                                     tmp / "anframes2")
            if len(txt) < 50:
                raise RuntimeError(f"analisi vuota: {txt[:60]!r}")
            _report("analisi video (frame + AI)", "OK",
                    f"{len(fr)} frame · analisi {len(txt)} caratteri")
    except ModuleNotFoundError as exc:
        _report("analisi video (frame + AI)", "SKIP", str(exc))
    except Exception as exc:
        _env = _env_fail_msg(exc)
        _report("analisi video (frame + AI)", "SKIP" if _env else "FAIL",
                _env or str(exc))
    finally:
        try:
            import shutil as _sh2
            _sh2.rmtree(tmp / "anframes", ignore_errors=True)
            _sh2.rmtree(tmp / "anframes2", ignore_errors=True)
            (tmp / "selftest_anvideo.mp4").unlink(missing_ok=True)
        except OSError:
            pass

    # 18. trascrizione offline (faster-whisper + Argos) -------------------
    off18 = None
    try:
        import faster_whisper                      # noqa: F401
    except ImportError:
        _report("trascrizione offline (whisper locale)", "SKIP",
                "faster-whisper non installato")
    else:
        try:
            # modello in modelli/whisper accanto all'app (o vecchia cache HF)
            try:
                from .ai import local_models as _lm18
                small_dir = _lm18.whisper_path("small")
            except Exception:
                small_dir = (Path.home() / ".cache" / "huggingface" / "hub" /
                             "models--Systran--faster-whisper-small")
            if not small_dir.is_dir():
                _report("trascrizione offline (whisper locale)", "SKIP",
                        "modello small non scaricato (al primo uso online)")
            elif not has_voice:
                _report("trascrizione offline (whisper locale)", "SKIP",
                        "wav di test non disponibile")
            else:
                from .ai import offline as off18
                r = off18.transcribe({"model": "small"},
                                     voice_wav.read_bytes(),
                                     duration=6.0, start=0.0,
                                     bilingual=True)
                it18 = (r.get("italian") or "").strip()
                org18 = (r.get("original") or "").strip()
                if not it18 and not org18:
                    raise RuntimeError(f"trascrizione vuota: {r}")
                _report("trascrizione offline (whisper locale)", "OK",
                        f"lingua={r['language']} car={len(it18 or org18)} "
                        f"trad={'si' if it18 and org18 else 'no'}")
        except Exception as exc:
            _report("trascrizione offline (whisper locale)", "FAIL",
                    str(exc))
        finally:
            # rilascio esplicito: evita il crash 0xC0000409 di ctranslate2
            # durante lo shutdown dell'interprete con modello GPU vivo
            if off18 is not None:
                try:
                    off18.release_all()
                except Exception:
                    pass

    # pulizia totale delle temporanee --------------------------------------
    for p in (voice_wav, tmp / "selftest_capture.wav",
              tmp / "selftest_transcript.md", tmp / "selftest_minute.docx",
              tmp / "registrazione_TESTREC.mp3",
              tmp / "registrazione_TESTREC.wav",
              tmp / "selftest_schermo.mp4"):
        _rm_file(p)

    # 19. gestione modelli locali (Whisper + Argos) -----------------------
    try:
        from .ai import local_models
        whisper = local_models.list_whisper()
        argos = local_models.list_argos()
        # Elenco stabile: 5 modelli Whisper e 4 pacchetti Argos
        if len(whisper) != 5 or len(argos) != 4:
            raise RuntimeError(f"elenchi errati: w={len(whisper)} a={len(argos)}")
        # I modelli si possono anche NON avere (cartella vuota per scelta):
        # quel che conta e' che la risoluzione punti a modelli/ dell'app
        if not str(local_models.whisper_path("small")).startswith(
                str(local_models.MODELS_DIR)):
            raise RuntimeError(
                f"whisper 'small' risolve fuori da {local_models.MODELS_DIR}")
        _report("gestione modelli locali", "OK",
                f"{sum(m.installed for m in whisper)}/5 whisper, "
                f"{sum(m.installed for m in argos)}/4 argos installati · "
                f"cartella: {local_models.MODELS_DIR}")
    except Exception as exc:
        _report("gestione modelli locali", "FAIL", str(exc))
    try:
        removed = config.purge_temp_sessions()
        print(f"pulizia finale: {removed} voci temporanee rimosse")
    except Exception:
        pass

    report = _write_report()
    fails = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    print(f"\nTotale: {len(RESULTS)} test, {fails} falliti, "
          f"{time.time() - t0:.1f}s — report: {report}")
    return 1 if fails else 0
