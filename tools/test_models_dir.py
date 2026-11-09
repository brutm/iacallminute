"""I modelli locali stanno in modelli/ accanto all'applicazione
(Whisper + Argos), con migrazione dalle vecchie cartelle nascoste.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["IACM_MODELS_DIR"] = str(Path(tempfile.mkdtemp(
    prefix="iacm_modelli_")))          # cartella isolata per il test

from app.ai import local_models as lm  # noqa: E402


def main() -> int:
    ok = 0

    # 1) la cartella dei modelli segue IACM_MODELS_DIR (niente home)
    assert lm.MODELS_DIR.name.startswith("iacm_modelli_"), lm.MODELS_DIR
    assert lm.WHISPER_DIR.name == "whisper" and lm.ARGOS_DIR.name == "argos"
    ok += 1
    print("1 modelli/ isolata ok:", lm.MODELS_DIR)

    # 2) senza modelli tutto risulta "non installato"
    assert all(not m.installed for m in lm.list_whisper()), "whisper"
    assert all(not m.installed for m in lm.list_argos()), "argos"
    ok += 1
    print("2 elenco iniziale vuoto ok")

    # 3) un modello "installato" viene visto con la sua dimensione
    fake = lm.whisper_path("tiny")
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "model.bin").write_bytes(b"0" * 2048)
    m = next(x for x in lm.list_whisper() if x.id == "tiny")
    assert m.installed and m.size_bytes == 2048, m
    ok += 1
    print("3 whisper rilevato in modelli/ ok")

    # 4) eliminazione: la riga torna "non installato"
    assert lm.delete_whisper("tiny") is True
    assert not fake.exists(), "cartella modello non rimossa"
    assert all(not m.installed for m in lm.list_whisper())
    ok += 1
    print("4 elimina whisper ok")

    # 5) migrazione da cache HF (Whisper) e pacchetti Argos
    old_hf = lm._HF_CACHE
    old_argos = lm._ARGOS_OLD
    fake_hub = Path(tempfile.mkdtemp(prefix="iacm_hub_"))
    fake_argos = Path(tempfile.mkdtemp(prefix="iacm_argos_"))
    try:
        # Whisper: struttura cache HF (blobs + snapshots/<rev>/file)
        repo = fake_hub / "models--Systran--faster-whisper-base"
        snap = repo / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"x" * 4096)
        (snap / "config.json").write_text("{}", encoding="utf-8")
        # Argos: cartella normale
        (fake_argos / "en_it").mkdir()
        (fake_argos / "en_it" / "model").write_bytes(b"y" * 1024)

        lm._HF_CACHE = fake_hub
        lm._ARGOS_OLD = fake_argos
        moved = lm.migrate_models()

        assert (lm.whisper_path("base") / "model.bin").is_file(), \
            "whisper non migrato"
        assert (lm.ARGOS_DIR / "en_it" / "model").is_file(), "argos non migrato"
        assert moved > 0, moved
        assert not repo.exists(), "vecchia cache HF non rimossa"
        ok += 1
        print("4 migrazione da cache nascosta ok (%d byte)" % moved)

        # 6) idempotente: seconda esecuzione non fa nulla di nuovo
        again = lm.migrate_models()
        assert again == 0, again
        ok += 1
        print("5 migrazione idempotente ok")

        # 7) Argos userà la cartella nuova (variabile d'ambiente)
        lm.set_argos_env()
        assert os.environ["ARGOS_TRANSLATE_PACKAGE_DIR"] == str(lm.ARGOS_DIR)
        ok += 1
        print("6 ARGOS_TRANSLATE_PACKAGE_DIR ok")
    finally:
        lm._HF_CACHE = old_hf
        lm._ARGOS_OLD = old_argos
        shutil.rmtree(fake_hub, ignore_errors=True)
        shutil.rmtree(fake_argos, ignore_errors=True)
        shutil.rmtree(lm.MODELS_DIR, ignore_errors=True)

    print("PASS %d/7 test_models_dir" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())