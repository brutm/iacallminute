"""Segmentazione del parlato basata su energia (VAD semplice e robusto)."""
from __future__ import annotations

from collections import deque

import numpy as np

from .. import config

FRAME = config.TARGET_RATE * config.FRAME_MS // 1000       # campioni per frame (~480)
PREROLL_FRAMES = max(1, config.PREROLL_MS // config.FRAME_MS)
HANG_FRAMES = max(1, config.VAD_HANG_FRAMES)
ON_FRAMES = max(1, config.VAD_ON_FRAMES)
MAX_FRAME_S = config.MAX_SEGMENT_S
WARMUP_FRAMES = max(1, 450 // config.FRAME_MS)            # ~0.45 s
NOISE_CAP = 400.0                 # stima iniziale massima del rumore (int16)


class Segmenter:
    """Alimentato con campioni int16 mono @16 kHz; emette segmenti parlato.

    feed() restituisce una lista di tuple (start_index, pcm_ndarray).
    """

    def __init__(self) -> None:
        self._partial = np.empty(0, dtype=np.int16)
        self._noise = float(config.VAD_MIN_RMS)
        self._warm: list[float] = []      # rms dei primi frame (stima rumore)
        self._state = "silence"          # silence | speech
        self._on_count = 0
        self._hang_count = 0
        self._preroll: deque[tuple[int, np.ndarray]] = deque(maxlen=PREROLL_FRAMES)
        self._acc: list[np.ndarray] = []
        self._speech_start = 0
        self._total = 0                   # campioni assoluti visti

    def feed(self, samples: np.ndarray, start_index: int) -> list[tuple[int, np.ndarray]]:
        out: list[tuple[int, np.ndarray]] = []
        if samples.size == 0:
            return out
        data = np.concatenate([self._partial, samples]) if self._partial.size else samples
        n_frames = data.size // FRAME
        consumed = n_frames * FRAME
        self._partial = data[consumed:]

        for i in range(n_frames):
            frame = data[i * FRAME:(i + 1) * FRAME]
            idx = start_index + i * FRAME
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            if len(self._warm) < WARMUP_FRAMES:
                # avvio: si impara il rumore di fondo (mediana) prima di
                # poter aprire un parlato, così un fondo costante non
                # diventa subito un falso segmento
                self._warm.append(rms)
                # 20° percentile con tetto: se si parte già a metà frase la
                # stima non sale al livello del parlato
                self._noise = min(NOISE_CAP, max(
                    1.0, float(np.percentile(self._warm, 20))))
                self._preroll.append((idx, frame))
                continue
            thresh = max(config.VAD_MIN_RMS, self._noise * config.VAD_NOISE_MULT)
            voice = rms > thresh

            if self._state == "silence":
                # rumore di fondo: scende in fretta, sale piano (così un
                # fondo stazionario più alto della stima iniziale viene
                # imparato invece di sembrare parlato continuo)
                if rms < self._noise:
                    self._noise = 0.9 * self._noise + 0.1 * rms
                elif not voice:
                    self._noise = 0.99 * self._noise + 0.01 * rms
                else:
                    self._noise = 0.998 * self._noise + 0.002 * rms
                self._preroll.append((idx, frame))
                if voice:
                    self._on_count += 1
                else:
                    self._on_count = 0
                if self._on_count >= ON_FRAMES:
                    self._state = "speech"
                    self._hang_count = 0
                    self._acc = [fr for _, fr in self._preroll] + [frame]
                    first_idx = self._preroll[0][0] if self._preroll else idx
                    self._speech_start = first_idx
                    self._preroll.clear()
            else:  # speech
                self._acc.append(frame)
                if voice:
                    self._hang_count = 0
                else:
                    self._hang_count += 1
                # split forzato per durata massima
                seg_len = idx + FRAME - self._speech_start
                if seg_len >= int(MAX_FRAME_S * config.TARGET_RATE):
                    seg = self._flush(trim_tail=0)
                    if seg is not None:
                        out.append(seg)
                    self._state = "speech"
                    self._hang_count = 0
                    self._acc = []
                    self._speech_start = idx + FRAME
                elif self._hang_count >= HANG_FRAMES:
                    seg = self._flush(trim_tail=HANG_FRAMES - 3)
                    if seg is not None:
                        out.append(seg)
                    self._state = "silence"
                    self._on_count = 0
                    self._hang_count = 0

        self._total = start_index + consumed
        return out

    def _flush(self, trim_tail: int) -> tuple[int, np.ndarray] | None:
        if not self._acc:
            return None
        pcm = np.concatenate(self._acc)
        if trim_tail > 0 and pcm.size > trim_tail * FRAME:
            pcm = pcm[: -trim_tail * FRAME]
        self._acc = []
        duration = pcm.size / config.TARGET_RATE
        if duration < config.SEGMENT_MIN_S:
            return None
        return self._speech_start, pcm

    def flush(self) -> list[tuple[int, np.ndarray]]:
        """Chiude un eventuale parlato in corso (fine registrazione)."""
        if self._state != "speech":
            return []
        seg = self._flush(trim_tail=0)
        self._state = "silence"
        self._on_count = 0
        self._hang_count = 0
        return [seg] if seg is not None else []
