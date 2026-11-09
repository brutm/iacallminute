"""Gestione dei modelli locali (Whisper + Argos) installati su disco.

I modelli vivono in `modelli/` **accanto all'applicazione**: nella radice del
progetto in sviluppo, nella stessa cartella dell'exe quando e' impacchettato.
Niente cache nascoste in home (niente piu' ~/.cache/huggingface ne'
~/.local/share/argos-translate). Se la cartella dell'app non e' scrivibile
(per es. installazione in Program Files) si ripiega su
%APPDATA%\\IACallMinute\\modelli.

  modelli/whisper/<nome>/   modello Whisper (es. Systran/faster-whisper-small)
  modelli/argos/<src>_it/   pacchetto di traduzione Argos
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import config


def _app_dir() -> Path:
    """Cartella dell'applicazione: l'exe se impacchettato, altrimenti la
    radice del progetto."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".scrivi_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_models_dir() -> Path:
    env = os.environ.get("IACM_MODELS_DIR", "").strip()
    if env:
        return Path(os.path.expandvars(env))
    accanto = _app_dir() / "modelli"
    if _writable(accanto):
        return accanto
    return config.APPDATA_DIR / "modelli"


MODELS_DIR = _resolve_models_dir()
WHISPER_DIR = MODELS_DIR / "whisper"
ARGOS_DIR = MODELS_DIR / "argos"

# Vecchie posizioni: servono solo per la migrazione automatica
_HF_CACHE = Path(os.environ.get("HF_HUB_CACHE", "") or (
    Path.home() / ".cache" / "huggingface" / "hub"))
_ARGOS_OLD = Path(os.environ.get("XDG_DATA_HOME", "") or (
    Path.home() / ".local" / "share")) / "argos-translate" / "packages"


def whisper_path(name: str) -> Path:
    """Cartella del modello Whisper `name` dentro modelli/."""
    return WHISPER_DIR / name


def ensure_dirs() -> None:
    for p in (MODELS_DIR, WHISPER_DIR, ARGOS_DIR):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def set_argos_env() -> str:
    """Indica ad Argos dove mettere i pacchetti (prima dell'import)."""
    os.environ["ARGOS_TRANSLATE_PACKAGE_DIR"] = str(ARGOS_DIR)
    return str(ARGOS_DIR)


# --- Argos --------------------------------------------------------------
def _argos_pkg_dir() -> Path:
    return ARGOS_DIR


# Stima "ufficiale" delle dimensioni di un modello (per UI prima del download)
WHISPER_SIZE_HINT_MB: dict[str, int] = {
    "tiny": 75,
    "base": 142,
    "small": 466,
    "medium": 1530,
    "large-v3": 3094,
}


def _dir_size(p: Path) -> int:
    total = 0
    try:
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


@dataclass
class LocalModel:
    id: str               # "small", "en_it", …
    kind: str             # "whisper" | "argos"
    label: str            # "Whisper small (≈466 MB)"
    installed: bool
    size_bytes: int = 0
    hint_mb: int = 0      # stima prima del download
    busy: bool = False
    progress: float = 0.0
    error: str = ""
    extras: dict = field(default_factory=dict)


def _installed_whisper_dir(p: Path) -> bool:
    """True se la cartella contiene davvero un modello Whisper usabile."""
    try:
        return p.is_dir() and any(
            (p / f).is_file() for f in ("model.bin", "model.safetensors"))
    except OSError:
        return False


def list_whisper() -> list[LocalModel]:
    out: list[LocalModel] = []
    for name in ("tiny", "base", "small", "medium", "large-v3"):
        p = whisper_path(name)
        installed = _installed_whisper_dir(p)
        sz = _dir_size(p) if installed else 0
        out.append(LocalModel(
            id=name,
            kind="whisper",
            label=f"Whisper {name}" + ("  (consigliato)" if name == "small" else ""),
            installed=sz > 0,
            size_bytes=sz,
            hint_mb=WHISPER_SIZE_HINT_MB.get(name, 0),
        ))
    return out


# Lingue supportate (it = destinazione fissa)
ARGOS_LANGS = (
    ("en", "Inglese → Italiano"),
    ("fr", "Francese → Italiano"),
    ("de", "Tedesco → Italiano"),
    ("es", "Spagnolo → Italiano"),
)


def list_argos() -> list[LocalModel]:
    base = _argos_pkg_dir()
    out: list[LocalModel] = []
    for code, label in ARGOS_LANGS:
        pkg = base / f"{code}_it"
        sz = _dir_size(pkg) if pkg.is_dir() else 0
        out.append(LocalModel(
            id=f"{code}_it",
            kind="argos",
            label=label,
            installed=sz > 0,
            size_bytes=sz,
            # Stima approssimativa: ~70-100 MB per coppia
            hint_mb=90,
        ))
    return out


def delete_whisper(name: str) -> bool:
    """Rimuove il modello Whisper da modelli/whisper/."""
    if name not in WHISPER_SIZE_HINT_MB:
        return False
    p = whisper_path(name)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    # Invalidare le istanze dell'offline module (chiave "nome@device")
    try:
        from . import offline
        for k in [k for k in offline._models if k.startswith(name + "@")]:
            offline._models.pop(k, None)
    except Exception:
        pass
    return True


def delete_argos(pair: str) -> bool:
    base = _argos_pkg_dir()
    pkg = base / pair
    if pkg.is_dir():
        shutil.rmtree(pkg, ignore_errors=True)
    return True


def download_whisper(name: str, on_progress: Callable[[float, str], None] | None = None,
                     on_done: Callable[[bool, str], None] | None = None) -> threading.Thread:
    """Scarica il modello Whisper in background dentro modelli/whisper/<nome>."""
    def _job() -> None:
        try:
            if on_progress:
                on_progress(0.02, f"connessione a HuggingFace…")
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                if on_done:
                    on_done(False, "pacchetto 'huggingface_hub' non installato")
                return
            ensure_dirs()
            snapshot_download(
                repo_id=f"Systran/faster-whisper-{name}",
                local_dir=str(whisper_path(name)),
                tqdm_class=None,
            )
            if on_done:
                on_done(True, f"modello '{name}' scaricato")
        except Exception as exc:
            if on_done:
                on_done(False, f"download fallito: {exc}")

    t = threading.Thread(target=_job, daemon=True, name=f"iacm-dl-whisper-{name}")
    t.start()
    return t


def download_argos(pair: str, on_progress: Callable[[float, str], None] | None = None,
                   on_done: Callable[[bool, str], None] | None = None) -> threading.Thread:
    """Scarica e installa il pacchetto Argos (src→it) in background."""
    src, dst = pair.split("_", 1)

    def _job() -> None:
        try:
            if on_progress:
                on_progress(0.1, f"scarico indice pacchetti Argos…")
            ensure_dirs()
            set_argos_env()      # prima dell'import di argostranslate
            try:
                from argostranslate import package as argos_pkg
            except ImportError:
                if on_done:
                    on_done(False, "pacchetto 'argostranslate' non installato")
                return
            argos_pkg.update_package_index()
            cand = next(
                (p for p in argos_pkg.get_available_packages()
                 if p.from_code == src and p.to_code == dst),
                None)
            if cand is None:
                if on_done:
                    on_done(False, f"pacchetto '{pair}' non disponibile sull'indice")
                return
            if on_progress:
                on_progress(0.5, f"download {cand.from_code}→{cand.to_code}…")
            path = cand.download()
            if on_progress:
                on_progress(0.85, "installazione…")
            argos_pkg.install_from_path(path)
            # Invalidare cache coppie
            try:
                from . import offline
                offline._pairs_done.discard((src, dst))
            except Exception:
                pass
            if on_done:
                on_done(True, f"pacchetto '{pair}' installato")
        except Exception as exc:
            if on_done:
                on_done(False, f"installazione fallita: {exc}")

    t = threading.Thread(target=_job, daemon=True, name=f"iacm-dl-argos-{pair}")
    t.start()
    return t


# --- DLL CUDA per la GPU di Whisper (opzionale) --------------------------- #
# CTranslate2 (il motore di faster-whisper) su Windows carica cublas/cuDNN
# via LoadLibrary: le DLL vanno rese visibili nel PATH. Nessuna installazione
# nel sistema: i wheel ufficiali NVIDIA di PyPI vengono scaricati, le DLL
# estratte qui dentro e i wheel cancellati. Cancellabile col progetto.
CUDA_DLL_DIR = MODELS_DIR / "cuda_dlls"
_CUDA_SIZE_HINT_MB = 1830      # cublas + cudnn, per l'etichetta della UI


def cuda_dll_present() -> bool:
    try:
        return (CUDA_DLL_DIR / "cublas64_12.dll").is_file() and any(
            CUDA_DLL_DIR.glob("cudnn64_*.dll"))
    except OSError:
        return False


def delete_cuda_dlls() -> bool:
    if not CUDA_DLL_DIR.is_dir():
        return False
    shutil.rmtree(CUDA_DLL_DIR, ignore_errors=True)
    return True


def _download_cuda_wheel(pkg: str, on_progress, base: float,
                         span: float, tmp: Path) -> None:
    """Scarica un wheel di PyPI con progressione e lo salva in tmp."""
    import requests
    resp = requests.get(f"https://pypi.org/pypi/{pkg}/json", timeout=20)
    resp.raise_for_status()
    files = resp.json().get("urls", [])
    whl = next((u for u in files
                if u.get("filename", "").endswith("-py3-none-win_amd64.whl")),
               None)
    if whl is None:
        raise RuntimeError(f"nessun wheel Windows per {pkg}")
    url, size = whl["url"], int(whl.get("size") or 0)
    with requests.get(url, stream=True, timeout=(20, 120)) as r:
        r.raise_for_status()
        total = size or int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1024 * 512):
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(base + span * done / total,
                                f"download {pkg.replace('-', ' ')} "
                                f"{done / 1e6:.0f}/{total / 1e6:.0f} MB…")


def download_cuda_dlls(on_progress: Callable[[float, str], None] | None = None,
                       on_done: Callable[[bool, str], None] | None = None) -> threading.Thread:
    """Scarica e estrae le DLL CUDA/cuDNN in modelli/cuda_dlls (background).

    Niente installazione: i wheel (~1,8 GB totali) vengono cancellati
    dopo l'estrazione; la cartella desapare col progetto.
    """
    def _job() -> None:
        try:
            import zipfile
            if on_progress:
                on_progress(0.02, "connessione a PyPI…")
            ensure_dirs()
            if CUDA_DLL_DIR.exists():
                shutil.rmtree(CUDA_DLL_DIR, ignore_errors=True)  # niente versione mescolata
            CUDA_DLL_DIR.mkdir(parents=True, exist_ok=True)
            tmp_root = config.get_temp_root() / f"dll_cuda_{int(time.time())}"
            tmp_root.mkdir(parents=True, exist_ok=True)
            try:
                n = len(_CUDA_WHEELS)
                for i, (pkg, _) in enumerate(_CUDA_WHEELS):
                    base, span = (0.05 + 0.40 * i / n, 0.40 / n)
                    tmp = tmp_root / f"{pkg}.whl"
                    _download_cuda_wheel(pkg, on_progress, base, span, tmp)
                    if on_progress:
                        on_progress(0.45 + 0.40 * i / n,
                                    f"estrazione {pkg}…")
                    with zipfile.ZipFile(tmp) as z:
                        for name in z.namelist():
                            if (name.endswith(".dll")
                                    and name.rsplit("/", 1)[-1]):
                                (CUDA_DLL_DIR / name.rsplit("/", 1)[-1])\
                                    .write_bytes(z.read(name))
                    tmp.unlink(missing_ok=True)
                if not cuda_dll_present():
                    raise RuntimeError(
                        "estrazione incompleta (cublas/cudnn mancanti)")
            finally:
                shutil.rmtree(tmp_root, ignore_errors=True)
            if on_done:
                on_done(True, f"DLL CUDA installate in {CUDA_DLL_DIR}")
        except Exception as exc:
            if on_done:
                on_done(False, f"download DLL CUDA fallito: {exc}")

    t = threading.Thread(target=_job, daemon=True,
                         name="iacm-dl-cuda-dlls")
    t.start()
    return t


def migrate_models() -> int:
    """Porta in modelli/ i modelli gia' scaricati nelle vecchie cartelle
    nascoste (nessun riscaricamento).

    - Whisper: ~/.cache/huggingface/hub/models--Systran--faster-whisper-*
      (la cache HF e' fatta di blob+snapshot: si riusano i file gia'
      scaricati scrivendoli nella destinazione, senza traffico di rete)
    - Argos:   ~/.local/share/argos-translate/packages/<src>_it

    Ritorna i byte spostati. Idempotente.
    """
    import logging
    log = logging.getLogger("iacm.modelli")
    ensure_dirs()
    moved = 0

    # --- Argos: spostamento puro di cartelle normali ---
    try:
        if _ARGOS_OLD.is_dir() and _ARGOS_OLD != ARGOS_DIR:
            for src in _ARGOS_OLD.iterdir():
                dst = ARGOS_DIR / src.name
                if not src.is_dir() or dst.exists():
                    continue
                try:
                    shutil.move(str(src), str(dst))
                    moved += _dir_size(dst)
                    log.info("pacchetto Argos spostato in %s", dst)
                except OSError:
                    continue
    except OSError:
        pass

    # --- Whisper: copia dei file gia' presenti (nessuna rete) ---
    for name in WHISPER_SIZE_HINT_MB:
        repo = _HF_CACHE / f"models--Systran--faster-whisper-{name}"
        if not repo.is_dir():
            continue
        target = whisper_path(name)
        try:
            if _installed_whisper_dir(target):
                shutil.rmtree(repo, ignore_errors=True)   # gia' spostato
                continue
            snaps = sorted(repo.glob("snapshots/*"),
                           key=lambda p: p.stat().st_mtime)
            if snaps:
                # nella cache HF i file sono symlink/blob: copy2 segue i
                # link e porta il contenuto reale in modelli/
                target.mkdir(parents=True, exist_ok=True)
                for src in snaps[-1].iterdir():
                    dest = target / src.name
                    if dest.exists():
                        continue
                    if src.is_dir():
                        shutil.copytree(src, dest, symlinks=False)
                    else:
                        shutil.copy2(src, dest)
            if not _installed_whisper_dir(target):
                # cache incompleta/illeggibile: solo allora si ritenta
                # scaricando davvero (serve la rete)
                from huggingface_hub import snapshot_download
                snapshot_download(repo_id=f"Systran/faster-whisper-{name}",
                                  cache_dir=str(_HF_CACHE),
                                  local_dir=str(target), tqdm_class=None)
            if _installed_whisper_dir(target):
                moved += _dir_size(target)
                shutil.rmtree(repo, ignore_errors=True)
                log.info("modello Whisper '%s' spostato in %s", name, target)
        except Exception:
            log.debug("migrazione Whisper '%s' saltata", name, exc_info=True)
            continue
    return moved
