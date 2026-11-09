"""Pulizia delle sessioni temporanee non salvate (usata anche all'avvio)."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402


def main() -> int:
    ok = 0
    root = Path(tempfile.mkdtemp(prefix="iacm_purge_"))
    try:
        # 1) sessioni e selftest: devono sparire
        for name in ("session_20260101_120000", "session_20260101_130000",
                     "selftest_capture.wav"):
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / "registrazione.wav").write_bytes(b"x" * 5000)
        # 2) roba dell'utente: NON deve sparire
        for name in ("documenti", "mia_cartella"):
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / "relazione.docx").write_bytes(b"x" * 100)
        (root / "note.txt").write_text("mia", encoding="utf-8")

        n = config.purge_temp_sessions(root, quiet=True)
        left = sorted(p.name for p in root.iterdir())
        print("1) rimosse %d voci, rimaste: %s" % (n, left))
        assert n == 3, n
        assert left == ["documenti", "mia_cartella", "note.txt"], left
        ok += 1

        # 2) seconda passata: niente da fare (idempotente)
        n2 = config.purge_temp_sessions(root, quiet=True)
        assert n2 == 0, n2
        ok += 1
        print("2) idempotente ok")

        # 3) cartella inesistente: nessun errore
        assert config.purge_temp_sessions(root / "non_esiste",
                                          quiet=True) == 0
        ok += 1
        print("3) cartella assente ok")

        # 4) l'avvio dell'app chiama la pulizia
        src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
            encoding="utf-8")
        assert "config.purge_temp_sessions()" in src, \
            "main.py non pulisce all'avvio"
        ok += 1
        print("4) pulizia collegata all'avvio ok")

        # 5) le chiavi salvate non vengono toccate dalla pulizia
        marker = root / "settings.json"
        marker.write_text('{"text": {"api_key": "segreta"}}', encoding="utf-8")
        config.purge_temp_sessions(root, quiet=True)
        assert marker.is_file(), "settings.json non deve essere toccato"
        ok += 1
        print("5) settings non toccato ok")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("PASS %d/5 test_purge_temp" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())