"""Analisi differita: conversione WAV, suddivisione in blocchi, annullamento,
generazione con gli stessi prompt delle sessioni live. Nessuna rete.
"""
import io
import os
import sys
import threading
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from app import config  # noqa: E402
from app.ai import deferred, service  # noqa: E402
from app.ai.errors import ProviderError  # noqa: E402

RATE = config.TARGET_RATE


def _make_wav(path: Path, seconds: float) -> Path:
    ring = int(RATE * seconds)
    t = np.arange(ring, dtype=np.float32) / RATE
    sig = (2500 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)  # tono finto
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(sig.tobytes())
    return path


def main() -> int:
    ok = 0
    tmp = Path(tempfile.mkdtemp(prefix="iacm_test_deferred_"))
    try:
        # 1) formati ammessi
        assert deferred.is_supported(Path("x.mp4"))
        assert deferred.is_supported(Path("x.wav"))
        assert deferred.is_video(Path("x.mp4"))
        assert not deferred.is_video(Path("x.mp3"))
        assert not deferred.is_supported(Path("x.txt"))
        ok += 1
        print("1) formati ammessi ok")

        # 2) a WAV gia' a 16k/mono/int16 NON serve ffmpeg
        src = _make_wav(tmp / "call_20260101_101010.wav", 10.0)
        wav, dur = deferred.to_wav(src, tmp)
        assert wav == src and abs(dur - 10.0) < 0.05, (wav, dur)
        ok += 1
        print("2) WAV sorgente passthrough ok (%.1fs)" % dur)

        # 3) blocchi: 10s a 3s +monoscopped... = 4 blocchi con inizio atteso
        chunks = deferred.chunk_wav(wav, chunk_s=3.0)
        starts = [round(s, 3) for s, _ in chunks]
        assert starts == [0.0, 3.0, 6.0, 9.0], starts
        for s, b in chunks:
            with wave.open(io.BytesIO(b), "rb") as w:
                d = w.getnframes() / RATE
                assert 0.9 < d <= 3.05, (s, d)
        ok += 1
        print("3) suddivisione in blocchi ok: %d blocchi %s" % (len(chunks),
                                                               starts))

        # 4) trascrizione a blocchi con finto provider (conta le chiamate)
        calls: list = []

        def fake_stt(settings, wav_bytes, *, duration, start, bilingual):
            calls.append(start)
            with wave.open(io.BytesIO(wav_bytes), "rb") as w:
                assert w.getframerate() == RATE
            return {"language": "it", "italian": f"blocco {start:.0f}",
                    "original": ""}

        service.transcribe_segment = fake_stt
        data = deferred.transcribe_file(
            {"stt": {"type": "gemini"}, "text": {"type": "gemini"}},
            src, tmp / "work", chunk_s=3.0)
        assert len(calls) == 4 and abs(data["duration"] - 10.0) < 0.05
        assert "[00:00] blocco 0" in data["transcript"], data["transcript"]
        assert "[00:09] blocco 9" in data["transcript"]
        ok += 1
        print("4) trascrizione differita ok (%d blocchi)" % len(calls))

        # 5) annullamento: cancel gia' settato -> errore gestito
        c = threading.Event()
        c.set()
        try:
            deferred.transcribe_file(
                {"stt": {"type": "gemini"}, "text": {"type": "gemini"}},
                src, tmp / "work", chunk_s=3.0, cancel=c)
            raise AssertionError("doveva annullare")
        except ProviderError as exc:
            assert "annullata" in str(exc), exc
        ok += 1
        print("5) annullamento attraverso tutto ok")

        # 6) generazione: stessi prompt, con linea sulla provenienza
        captured: dict = {}

        def fake_complete(settings, system, user, on_status=None):
            captured["system"] = system
            captured["user"] = user
            return "# documento finto"

        service.complete = fake_complete
        fa = deferred.generate_document(
            {"stt": {"type": "gemini"}, "text": {"type": "gemini"}},
            "summary", data, src)
        assert fa == "# documento finto"
        assert "DIFFERITA" in captured["user"], captured["user"][:200]
        assert "call_20260101_101010.wav" in captured["user"]
        assert "Punti chiave" in captured["user"]
        ok += 1
        print("6) prompt del riassunto differito ok")

        deferred.generate_document(
            {"stt": {"type": "gemini"}, "text": {"type": "gemini"}},
            "minutes", data, src)
        assert "Minute della riunione" in captured["user"]
        assert "Discussioni" in captured["user"]
        ok += 1
        print("7) prompt delle minute differite ok")

        # 8) timbro dal nome del file
        assert deferred.source_stamp(src) == "20260101_101010"
        ok += 1
        print("8) timbro dal nome file ok")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("PASS %d/8 test_deferred" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
