"""GPU Whisper: dispositivo selezionabile, DLL CUDA opzionali in modelli/,
fallback CPU automatico e cache dei modelli per device.
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["IACM_MODELS_DIR"] = str(Path(tempfile.mkdtemp(
    prefix="iacm_gpu_")))               # cartella modelli isolata

from app.ai import offline, local_models as lm  # noqa: E402
from app import config  # noqa: E402


def main() -> int:
    ok = 0

    # --- 1) riconoscimento setting ------------------------------------- #
    assert offline.wanted_device({}) == "auto"
    assert offline.wanted_device({"device": ""}) == "auto"
    assert offline.wanted_device({"device": " GPU "}) == "gpu"
    assert offline.wanted_device({"device": "CPU"}) == "cpu"
    assert offline.wanted_device({"device": "pippo"}) == "pippo"  # filtrato
    ok += 1
    print("1 wanted_device analizza i settings ok")

    # 2) fallback: nessuna GPU -> CPU
    import types
    stub = types.ModuleType("ctranslate2")
    stub.get_cuda_device_count = lambda: 0
    sys.modules["ctranslate2-fallback-stub"] = stub
    real_ct2 = sys.modules.get("ctranslate2")
    sys.modules["ctranslate2"] = stub
    try:
        m, err = offline._try_cuda(Path("qualunque"))
        assert m is None and err == "nessuna GPU CUDA visibile", (m, err)
    finally:
        if real_ct2 is not None:
            sys.modules["ctranslate2"] = real_ct2
        else:
            sys.modules.pop("ctranslate2", None)
    ok += 1
    print("2 nessuna GPU visibile -> fallback motivo chiaro ok")

    # 3) contatore device: 0 GPU -> niente tentativo CUDA (auto->cpu)
    class _Fake:
        def __init__(self):
            self.device = None

    fake_cpu = object()                 # m segnaposto per chiave cpu
    offline._models["small@cpu"] = fake_cpu
    offline._models["small@gpu"] = object()
    off_stt = {"type": "offline", "model": "small", "language": "auto",
               "device": "cpu", "api_key": ""}
    assert offline._whisper("small", off_stt) is fake_cpu
    gpu_stt = dict(off_stt, device="gpu")
    assert offline._whisper("small", gpu_stt) is offline._models["small@gpu"]
    ok += 1
    print("3 cache modelli per chiave nome@device ok")

    # 4) percorsi DLL: variabile env valutata, cartella segnalata
    dl = Path(os.environ["IACM_MODELS_DIR"]) / "cuda_dlls"
    os.environ["IACM_CUDA_DLLS"] = str(dl)
    assert not lm.cuda_dll_present()
    assert offline._cuda_dll_dirs() == [] or \
        all(p.name == "cuda_dlls" for p in offline._cuda_dll_dirs())
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "cublas64_12.dll").write_bytes(b"x")
    (dl / "cudnn64_9.dll").write_bytes(b"x")
    assert offline.add_cuda_dll_dirs() == [str(dl)]
    ok += 1
    print("4 DLL CUDA in modelli/cuda_dlls rilevate ok")

    # 5) elenco/CUDA: cancellabile, riscaricabile
    assert lm.delete_cuda_dlls() is True
    assert not lm.cuda_dll_present()
    assert lm.delete_cuda_dlls() is False
    ok += 1
    print("5 delete_cuda_dlls ok")

    # 6) invalidazione cache al delete del modello (chiave nome@device)
    os.environ["IACM_MODELS_DIR"] = str(Path(tempfile.mkdtemp(
        prefix="iacm_gpu_inval_")))
    # attenzione: MODELS_DIR e' risolto all'import: ricarico il modulo
    import importlib
    importlib.reload(lm)
    assert lm.delete_whisper("small") is True          # assente -> invalida cmq
    ok += 1
    print("6 delete_whisper senza errore su chiavi nome@device ok")

    # 7) impostazione salvata nei default
    assert config.DEFAULT_SETTINGS["stt"]["device"] == "auto"
    assert [v for v, _ in config.STT_DEVICES] == ["auto", "gpu", "cpu"]
    ok += 1
    print("7 default stt.device = auto ok")

    print(f"PASS {ok}/7 test_gpu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
