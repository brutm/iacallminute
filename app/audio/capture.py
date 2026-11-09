"""Cattura audio di sistema (WASAPI loopback) + microfono, mix a 16 kHz mono."""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - dipendenza assente
    pyaudio = None

from .. import config
from .segmenter import Segmenter


# --------------------------------------------------------------------------- #
# Dispositivi
# --------------------------------------------------------------------------- #
def list_audio_devices() -> dict:
    """Enumera loopback (sistema) e microfoni. Solleva RuntimeError se manca WASAPI."""
    if pyaudio is None:
        raise RuntimeError("pyaudiowpatch non installato")
    pa = pyaudio.PyAudio()
    loopbacks: list[dict] = []
    mics: list[dict] = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) <= 0:
                continue
            entry = {
                "index": i,
                "name": str(info.get("name", f"device {i}")),
                "rate": int(info.get("defaultSampleRate", 48000)),
                "channels": max(1, int(info.get("maxInputChannels", 1))),
            }
            if info.get("isLoopbackDevice"):
                loopbacks.append(entry)
            else:
                mics.append(entry)

        default_loopback = None
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            out = pa.get_device_info_by_index(int(wasapi["defaultOutputDevice"]))
            out_name = str(out.get("name", ""))
            for lb in loopbacks:
                if out_name and (out_name in lb["name"] or lb["name"] in out_name):
                    default_loopback = lb
                    break
        except Exception:
            pass
        if default_loopback is None and loopbacks:
            default_loopback = loopbacks[0]

        default_mic = None
        try:
            di = pa.get_default_input_device_info()
            for m in mics:
                if m["index"] == int(di["index"]):
                    default_mic = m
                    break
        except Exception:
            pass
        if default_mic is None and mics:
            default_mic = mics[0]
    finally:
        pa.terminate()

    return {
        "loopbacks": loopbacks,
        "mics": mics,
        "default_loopback": default_loopback,
        "default_mic": default_mic,
    }


# --------------------------------------------------------------------------- #
# Resampler streaming
# --------------------------------------------------------------------------- #
class Resampler:
    """Resampling lineare streaming (mantieni continuità tra i blocchi)."""

    def __init__(self, src_rate: int, dst_rate: int = config.TARGET_RATE):
        self.step = src_rate / dst_rate
        self._buf = np.empty(0, dtype=np.float32)
        self._pos = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0:
            return np.empty(0, dtype=np.int16)
        block = x.astype(np.float32)
        self._buf = np.concatenate([self._buf, block]) if self._buf.size else block
        n = self._buf.size
        k = int((n - 1 - self._pos) // self.step) if n > 1 else 0
        if k > 0:
            positions = self._pos + self.step * np.arange(k, dtype=np.float64)
            out = np.interp(positions, np.arange(n, dtype=np.float64), self._buf)
            self._pos += self.step * k
        else:
            out = np.empty(0, dtype=np.float32)
        keep = int(self._pos)
        if keep > 0:
            self._buf = self._buf[keep:]
            self._pos -= keep
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)


# --------------------------------------------------------------------------- #
# Sorgente audio (thread di lettura)
# --------------------------------------------------------------------------- #
class _Source(threading.Thread):
    def __init__(self, device: dict, stop_event: threading.Event,
                 on_error: Callable[[str], None], label: str):
        super().__init__(daemon=True, name=f"src-{label}")
        self.device = device
        self.stop_event = stop_event
        self.on_error = on_error
        self.label = label
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.opened = threading.Event()
        self.failed = False

    def pop(self, n_samples: int) -> np.ndarray:
        need = n_samples * 2
        with self.lock:
            take = min(need, len(self.buf)) & ~1
            if take:
                # una sola copia (prima: slice -> bytes -> frombuffer)
                arr = np.frombuffer(self.buf, dtype=np.int16,
                                    count=take // 2).copy()
                del self.buf[:take]
            else:
                arr = np.empty(0, np.int16)
        if arr.size < n_samples:
            arr = np.concatenate([arr, np.zeros(n_samples - arr.size, dtype=np.int16)])
        return arr

    def available_samples(self) -> int:
        with self.lock:
            return len(self.buf) // 2

    def run(self) -> None:
        if pyaudio is None:
            self.failed = True
            self.on_error("pyaudiowpatch non installato")
            return
        pa = pyaudio.PyAudio()
        try:
            channels = min(2, max(1, int(self.device.get("channels", 1))))
            rate = int(self.device.get("rate", 48000))
            block = max(1, rate // 30)
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(self.device["index"]),
                frames_per_buffer=block,
            )
            resampler = Resampler(rate)
            max_bytes = int(config.MAX_BUFFER_S * config.TARGET_RATE) * 2
            self.opened.set()
            while not self.stop_event.is_set():
                try:
                    data = stream.read(block, exception_on_overflow=False)
                except (OSError, IOError):
                    break
                x = np.frombuffer(data, dtype=np.int16)
                if channels > 1:
                    x = x.reshape(-1, channels).astype(np.float32).mean(axis=1)
                    x = np.clip(np.round(x), -32768, 32767).astype(np.int16)
                mono = resampler.process(x)
                with self.lock:
                    self.buf.extend(mono.tobytes())
                    if len(self.buf) > max_bytes:
                        drop = len(self.buf) - max_bytes
                        del self.buf[:drop]
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        except Exception as exc:
            self.failed = True
            self.opened.set()
            self.on_error(f"audio {self.label} ({self.device.get('name')}): {exc}")
        finally:
            pa.terminate()


# --------------------------------------------------------------------------- #
# Sessione di cattura
# --------------------------------------------------------------------------- #
class AudioSession:
    """Cattura loopback + microfono, mix, scrittura WAV e produzione segmenti."""

    def __init__(
        self,
        loopback_device: dict,
        mic_device: dict | None,
        wav_path: Path | None,
        on_segment: Callable[[float, np.ndarray], None],
        on_level: Callable[[float], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ):
        self.loopback_device = loopback_device
        self.mic_device = mic_device
        self.wav_path = Path(wav_path) if wav_path else None
        self.on_segment = on_segment
        self.on_level = on_level
        self.on_error = on_error or (lambda m: None)
        self.on_status = on_status or (lambda m: None)

        self._stop = threading.Event()
        self._sources: list[_Source] = []
        self._mixer: threading.Thread | None = None
        self._wav: "wave.Wave_write | None" = None
        self._segmenter = Segmenter()
        self._sample_count = 0
        self._lock = threading.Lock()
        self.running = False
        self.started_at: float | None = None

    # -- ciclo di vita --------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._sample_count = 0
        self._segmenter = Segmenter()

        if self.wav_path is not None:
            self.wav_path.parent.mkdir(parents=True, exist_ok=True)
            self._wav = wave.open(str(self.wav_path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(config.TARGET_RATE)

        lb = _Source(self.loopback_device, self._stop, self.on_error, "sistema")
        self._sources.append(lb)
        lb.start()
        lb.opened.wait(timeout=5)

        if self.mic_device is not None:
            mic = _Source(self.mic_device, self._stop, self.on_error, "microfono")
            self._sources.append(mic)
            mic.start()
            mic.opened.wait(timeout=5)
            if mic.failed:
                self.on_error("microfono non disponibile: proseguo con l'audio di sistema")
                self._sources = [s for s in self._sources if s is not mic]

        if any(s.failed for s in self._sources[:1]):
            # il loopback è indispensabile
            self._cleanup_sources()
            if self._wav:
                self._wav.close()
                self._wav = None
            raise RuntimeError(
                "impossibile aprire il dispositivo audio di sistema (loopback)"
            )

        self.running = True
        self.started_at = time.monotonic()
        self._mixer = threading.Thread(target=self._mix_loop, daemon=True, name="mixer")
        self._mixer.start()
        self.on_status("cattura audio avviata")

    def stop(self) -> dict:
        if not self.running:
            return {"duration": 0.0, "samples": 0}
        self._stop.set()
        for s in self._sources:
            s.join(timeout=2)
        if self._mixer:
            self._mixer.join(timeout=5)
        self.running = False
        duration = self._sample_count / config.TARGET_RATE
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        self._sources = []
        self.on_status("cattura audio fermata")
        return {"duration": duration, "samples": self._sample_count}

    def _cleanup_sources(self) -> None:
        self._stop.set()
        for s in self._sources:
            s.join(timeout=2)
        self._sources = []

    # -- mixer ----------------------------------------------------------------
    def _mix_tick(self, n: int) -> tuple[np.ndarray, float]:
        parts = [s.pop(n) for s in self._sources]
        mixed = parts[0]
        for p in parts[1:]:
            mixed = np.clip(
                mixed.astype(np.int32) + p.astype(np.int32), -32768, 32767
            ).astype(np.int16)
        rms = float(np.sqrt(np.mean(mixed.astype(np.float32) ** 2))) if mixed.size else 0.0
        return mixed, rms / 32768.0

    def _emit(self, start_idx: int, pcm: np.ndarray) -> None:
        try:
            self.on_segment(start_idx / config.TARGET_RATE, pcm)
        except Exception as exc:
            self.on_error(f"errore gestione segmento: {exc}")

    def _mix_loop(self) -> None:
        n = config.TARGET_RATE * config.MIX_TICK_MS // 1000
        tick = config.MIX_TICK_MS / 1000.0
        next_t = time.monotonic()
        try:
            while not self._stop.is_set():
                mixed, level = self._mix_tick(n)
                with self._lock:
                    if self._wav is not None:
                        self._wav.writeframes(mixed.tobytes())
                    segs = self._segmenter.feed(mixed, self._sample_count)
                    self._sample_count += mixed.size
                if self.on_level:
                    try:
                        self.on_level(level)
                    except Exception:
                        pass
                for start_idx, pcm in segs:
                    self._emit(start_idx, pcm)
                next_t += tick
                delay = next_t - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
        finally:
            # drenaggio buffer residui + chiusura eventuale parlato
            try:
                while True:
                    remaining = max(s.available_samples() for s in self._sources) if self._sources else 0
                    if remaining <= 0:
                        break
                    mixed, _ = self._mix_tick(min(n, remaining))
                    if mixed.size == 0:
                        break
                    with self._lock:
                        if self._wav is not None:
                            self._wav.writeframes(mixed.tobytes())
                        segs = self._segmenter.feed(mixed, self._sample_count)
                        self._sample_count += mixed.size
                    for start_idx, pcm in segs:
                        self._emit(start_idx, pcm)
                with self._lock:
                    tail = self._segmenter.flush()
                for start_idx, pcm in tail:
                    self._emit(start_idx, pcm)
            except Exception as exc:
                self.on_error(f"chiusura cattura: {exc}")
