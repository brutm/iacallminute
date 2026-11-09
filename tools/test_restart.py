"""A1+A2: Whisper e' non thread-safe e la seconda sessione deve trascrivere.

1) Il lock di offline (_infer_lk) serializza le inferenze: con due worker
   paralleli sulla stessa istanza la concorrenza massima deve restare 1.
2) Lingua forzata: offline.transcribe passa "it" a Whisper e salta Argos.
3) Pipeline: sessione -> stop con drain -> nuova sessione -> entrambe
   trascrivono tutti i segmenti (prima la seconda restava muta).
A3) blocco lingua di sessione: consenso su 3 segmenti consecutivi ->
   lingua bloccata; lingua manuale vince; nuova Pipeline riparte in auto.
"""
import logging
import os
import sys
import threading
import time
import wave
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import config  # noqa: E402
from app.ai import offline, service  # noqa: E402
from app.transcription.pipeline import Pipeline  # noqa: E402

log = logging.getLogger("iacm.test")

# --- finto Whisper non thread-safe (come faster-whisper reale) ------------- #
_CONC_LK = threading.Lock()
_conc = 0
_conc_max = 0
_seen_languages: list = []


class _Seg:
    def __init__(self, text):
        self.text = text


class _Info:
    def __init__(self, lang):
        self.language = lang


class _FakeUnsafeWhisper:
    """Simula WhisperModel: due .transcribe() in parallelo corrompono."""

    def transcribe(self, audio, language=None, vad_filter=False, beam_size=1,
                   condition_on_previous_text=False):
        global _conc, _conc_max
        with _CONC_LK:
            _conc += 1
            _conc_max = max(_conc_max, _conc)
            _seen_languages.append(language)
        try:
            time.sleep(0.05)
            self._corrupt = _conc > 1          # come la reale spesso fa

            def _gen():
                time.sleep(0.25)
                if self._corrupt:
                    yield _Seg("")             # come la 2a sessione muta
                else:
                    yield _Seg("trascrizione pulita")

            return _gen(), _Info("it")
        finally:
            with _CONC_LK:
                _conc -= 1


def _wav_bytes(seconds=0.5) -> bytes:
    rate = config.TARGET_RATE
    frame = np.zeros(int(rate * seconds), dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frame.tobytes())
    return buf.getvalue()


def main() -> int:
    ok = 0
    WAV = _wav_bytes()

    # --- 1) lock: due worker paralleli restano serializzati -------------- #
    offline._whisper = lambda name, stt=None: _FakeUnsafeWhisper()
    offline._models["small@auto"] = _FakeUnsafeWhisper()  # gia' caricato
    results: list = []
    stt_off = {"type": "offline", "model": "small", "language": "auto",
               "api_key": ""}
    settings = {"stt": stt_off,
                "text": {"type": "gemini", "model": "x", "api_key": ""}}
    threads = [threading.Thread(
        target=lambda: results.append(offline.transcribe(
            settings, WAV, duration=1.0, start=0.0, bilingual=False,
            language="auto")),
        name=f"wh-{i}") for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    print("1) concorrenza massima durante le inferenze: %d" % _conc_max)
    assert _conc_max == 1, ("Whisper usato in parallelo: %d" % _conc_max)
    ok += 1

    # due risultati puliti (niente trascrizione vuota "muta")
    assert len(results) == 2, results
    assert all(r["italian"] == "trascrizione pulita" for r in results), results
    ok += 1
    print("2) entrambi i segmenti trascritti nonostante il doppio thread ok")

    # --- 2) lingua forzata passata a Whisper ----------------------------- #
    offline.transcribe(settings, WAV, duration=1.0, start=0.0,
                       bilingual=False, language="it")
    assert _seen_languages[-1] == "it", _seen_languages[-1]
    offline.transcribe(settings, WAV, duration=1.0, start=0.0,
                       bilingual=False, language="auto")
    assert _seen_languages[-1] is None, _seen_languages[-1]
    ok += 1
    print("3) lingua forzata 'it' -> Whisper, 'auto' -> None ok")

    # --- 3) pipeline: due sessioni consecutive ---------------------------- #
    DELAY = 0.15

    def fake_stt(settings, wav_bytes, *, duration, start, bilingual):
        # e' un singolo modello condiviso: se due worker entrano insieme
        # il lock di bello deve far si che nessuno esca vuoto
        time.sleep(DELAY)
        return {"language": "it",
                "italian": f"IT {start:.0f}",
                "original": ""}

    service.transcribe_segment = fake_stt
    settings2 = {"stt": {"type": "gemini", "model": "finto", "api_key": ""},
                 "text": {"type": "gemini", "model": "finto", "api_key": ""}}
    audio = np.zeros(int(3 * config.TARGET_RATE), dtype=np.int16)
    audio[: config.TARGET_RATE] = 1000

    total: list[dict] = []
    on_res = total.append
    for sess in range(2):
        p = Pipeline(settings2, on_result=on_res, on_status=lambda m: None,
                     on_error=lambda m: None, on_fault=lambda i: None)
        p.bilingual = False
        p.start()
        for i in range(4):
            p.submit(i * 3.0 + sess * 100.0, audio.copy())
        time.sleep(DELAY / 2)
        p.stop(drain=True, timeout=60)
        got = len([r for r in total if r["error"] is None])
        print("    session %d: %d trascrizioni cumulative" % (sess + 1, got))

    in_session = [r["start"] for r in total if r["error"] is None]
    assert len(in_session) == 8, ("%d/8 trascrizioni tra le due sessioni: %s"
                                  % (len(in_session), sorted(in_session)))
    ok += 1
    print("4) stop drain -> nuova sessione: 8/8 segmenti trascritti ok")

    alive = [t.name for t in threading.enumerate()
             if t.name.startswith("pipeline-") and t.is_alive()]
    assert not alive, alive
    ok += 1
    print("5) nessun thread della pipeline restato vivo ok")

    # --- 4) blocco lingua di sessione (A3) ------------------------------- #
    # NB: service.transcribe_segment e' la fake_stt di sopra, ma il
    # consenso vive su _session_lang/reset: si testa direttamente quella.
    service.reset_session_language()
    stt_auto = {"type": "offline", "language": "auto", "model": "small",
                "api_key": ""}
    got: list[str] = [service._session_lang(stt_auto) for _ in range(3)]
    assert got == ["auto"] * 3, got
    for _ in range(2):
        assert service._session_lang(stt_auto, "en") == "auto"
    got.append(service._session_lang(stt_auto, "en"))     # 3o consenso -> lock
    assert got == ["auto"] * 3 + ["en"], got
    assert service._session_lang(stt_auto) == "en"        # segmenti dopo: en
    assert service._session_lang(stt_auto, "nl") == "en"  # glitch ignorato
    ok += 1
    print("6) consenso 3 segmenti 'en' -> lingua di sessione bloccata ok")

    # lingua forzata manuale vince e non aggiorna il consenso
    stt_it = dict(stt_auto, language="it")
    assert service._session_lang(stt_it) == "it"
    assert service._session_lang(stt_it, "en") == "it"
    ok += 1
    print("7) lingua forzata manuale vince sul consenso ok")

    # reset: una nuova sessione riparte in auto (nuova Pipeline)
    service._session_lang(stt_auto, "en")   # (gia' bloccata: resta en)
    assert service._session_lang(stt_auto) == "en"
    service.reset_session_language()
    assert service._session_lang(stt_auto) == "auto"
    got2 = [service._session_lang(stt_auto, "en") for _ in range(2)]
    assert got2 == ["auto", "auto"], got2   # consenso ripreso da zero
    assert service._session_lang(stt_auto) == "auto"
    ok += 1
    print("8) reset sessione -> il rilevamento automatico riparte ok")

    # Pipeline.__init__ azzera il blocco (auto-lock per sessione)
    with service._LANG_LK:
        service._lang_locked = "en"
        service._lang_recent[:] = ["en", "en", "en"]
    Pipeline(settings2, on_result=lambda r: None)
    assert service._session_lang(stt_auto) == "auto"
    ok += 1
    print("9) nuova Pipeline: blocco lingua azzerato in __init__ ok")

    print("PASS %d/9 test_restart" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
