"""Registrazione schermo: cattura frame e codifica diretta H.264 (MP4).

Nessun file intermedio: i frame (BGRA) vengono inviati in pipe a ffmpeg
che codifica subito in ``.mp4`` (H.264/yuv420p), così su disco finisce solo
il video compresso.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from pathlib import Path

import mss
from .. import subproc
from . import recorder

log = logging.getLogger("iacm.screenrec")


def ffmpeg_bin() -> str:
    path = recorder.ffmpeg_path()
    if not path:
        raise RuntimeError(
            "ffmpeg non trovato (serve per la codifica MP4): installalo "
            "dalle impostazioni o mettilo nel PATH")
    return path


def list_windows(exclude_titles: tuple = ()) -> list:
    """Finestre visibili con titolo, ottimo per la modalità «finestra»."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    out: list[dict] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title or any(title.startswith(t) for t in exclude_titles):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 200 or h < 150:
            return True
        out.append({"hwnd": int(hwnd), "title": title[:90],
                    "rect": (int(r.left), int(r.top), int(w), int(h))})
        return True

    enum = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                              ctypes.c_void_p)
    user32.EnumWindows(enum(_cb), 0)
    return out


def video_duration(video: Path) -> float:
    """Durata in secondi (da ffmpeg -i)."""
    r = subproc.run([ffmpeg_bin(), "-hide_banner", "-i", str(video)],
                       capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)",
                  r.stderr or "")
    if not m:
        raise RuntimeError("durata del video non leggibile")
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
            + float(m.group(3)))


def _mmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def extract_frames(video: Path, out_dir: Path, count: int = 12,
                   width: int = 768) -> list:
    """Estrae fino a ``count`` frame JPEG equidistanti dal video.

    Ritorna [(timestamp_mm:ss, path)] in ordine cronologico.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    dur = video_duration(video)
    n = max(2, int(count))
    interval = max(0.5, dur / n)
    cmd = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps=1/{interval:.4f},scale={width}:-2",
        "-q:v", "4", str(out_dir / "frame_%04d.jpg"),
    ]
    subproc.run(cmd, check=True, capture_output=True, timeout=180)
    files = sorted(out_dir.glob("frame_*.jpg"))
    return [(_mmss(i * interval), path) for i, path in enumerate(files)]


def monitor_at(x: int, y: int) -> dict:
    """Monitor (rettangolo mss) che contiene il punto."""
    with mss.mss() as sct:
        for mon in sct.monitors[1:]:
            if (mon["left"] <= x < mon["left"] + mon["width"]
                    and mon["top"] <= y < mon["top"] + mon["height"]):
                return dict(mon)
        return dict(sct.monitors[0])


def _even(v: int) -> int:
    """Dimensione pari (richiesta da yuv420p)."""
    return max(2, v if v % 2 == 0 else v - 1)


def mux_audio(video: Path, audio_wav: Path, dest: Path, *,
              on_progress=None, cancel_event=None) -> Path | None:
    """Unisce la traccia audio della sessione al video (H.264 copy + AAC).

    Con on_progress(frac 0..1) si riceve l'avanzamento reale di ffmpeg
    (lettura di «-progress pipe:1»); con cancel_event settato il processo
    viene terminato e si ritorna None. La barra serve per le sessioni
    lunghe: il mux di un MP4 di vari GB dura minuti.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        total = video_duration(video)
    except Exception:
        total = 0.0
    cmd = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-i", str(audio_wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(dest),
    ]
    try:
        proc = subproc.popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    except Exception as exc:                    # noqa: BLE001
        log.warning("mux audio non riuscito: %s", exc)
        return None

    parsed: list[float] = []

    def _pump() -> None:
        for line in proc.stdout or ():
            if on_progress is None:
                continue
            # «out_time_ms» di -progress e' in realta' in MICROSECONDI
            m = re.match(r"out_time_(us|ms)=(-?\d+)", line.strip())
            if m and total > 0:
                v = int(m.group(2))
                t = v / 1e6 if m.group(1) == "us" else v / 1e3 / 1e6
                frac = min(1.0, max(0.0, t / total))
                parsed.append(frac)
                try:
                    on_progress(frac)
                except Exception:               # noqa: BLE001
                    pass

    pump = threading.Thread(target=_pump, daemon=True, name="mux-progress")
    pump.start()
    while True:
        try:
            rc = proc.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                log.info("mux audio annullato dall'utente")
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:               # noqa: BLE001
                    pass
                return None
    if rc != 0 or not dest.is_file() or dest.stat().st_size < 1000:
        # gli errori di ffmpeg finiscono nello stesso pipe di -progress:
        # senza stderr separato si riporta solo il codice d'uscita
        log.warning("mux audio fallito (rc=%s)", rc)
        return None
    log.info("mux audio ok: %s (%d byte)", dest, dest.stat().st_size)
    return dest


class ScreenRecorder(threading.Thread):
    """Thread di cattura: mss -> pipe ffmpeg -> .mp4 (codifica diretta)."""

    def __init__(self, rect: tuple, fps: int, out_path: Path,
                 on_error=None):
        super().__init__(daemon=True, name="screenrec")
        x, y, w, h = rect
        self.rect = (int(x), int(y), _even(int(w)), _even(int(h)))
        self.fps = max(5, min(30, int(fps)))
        self.out_path = Path(out_path)
        self.on_error = on_error
        self.frames = 0
        self.started_mono: float | None = None
        self.error: str | None = None
        self._stop_evt = threading.Event()
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        x, y, w, h = self.rect
        cmd = [
            ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "bgra",
            "-video_size", f"{w}x{h}", "-framerate", str(self.fps),
            "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(self.out_path),
        ]
        try:
            self._proc = subproc.popen(
                cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            self.started_mono = time.monotonic()
            interval = 1.0 / self.fps
            next_t = time.monotonic()
            with mss.mss() as sct:
                region = {"left": x, "top": y, "width": w, "height": h}
                while not self._stop_evt.is_set():
                    shot = sct.grab(region)
                    try:
                        # .raw è già il buffer BGRA: .bgra ne farebbe una
                        # copia per frame (decine di MB/s a tutto schermo)
                        self._proc.stdin.write(shot.raw)
                    except BrokenPipeError:
                        break
                    self.frames += 1
                    next_t += interval
                    delay = next_t - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    else:                       # indietro: ricomincio da ora
                        next_t = time.monotonic()
        except Exception as exc:                # noqa: BLE001
            self.error = str(exc)
            log.exception("registrazione schermo fallita")
            if self.on_error is not None:
                try:
                    self.on_error(str(exc))
                except Exception:
                    pass
        finally:
            self._finalize()

    def _finalize(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=30)
            rc = proc.returncode
        except Exception:                       # noqa: BLE001
            proc.kill()
            rc = -1
        if self.error or rc != 0 or self.frames < 2:
            if rc != 0 and not self.error:
                err = b""
                try:
                    err = proc.stderr.read() if proc.stderr else b""
                except Exception:               # noqa: BLE001
                    pass
                self.error = (err.decode("utf-8", "replace")[-300:]
                              or f"ffmpeg exit {rc}")
            try:
                self.out_path.unlink(missing_ok=True)
            except OSError:
                pass
            log.warning("video scartato: %s", self.error)
        else:
            log.info("schermo salvato: %s (%d frame, %dx%d @%dfps)",
                     self.out_path, self.frames, self.rect[2],
                     self.rect[3], self.fps)

    # ------------------------------------------------------------------ #
    def stop(self, timeout: float = 60.0) -> Path | None:
        """Ferma la cattura e attende la fine della codifica."""
        if not self.is_alive() and self.frames == 0:
            return None
        self._stop_evt.set()
        self.join(timeout=timeout)
        if self.error or not self.out_path.is_file():
            return None
        return self.out_path
