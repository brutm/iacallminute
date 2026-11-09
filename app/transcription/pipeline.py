"""Worker che trasforma i segmenti audio in trascrizioni tramite l'AI.

Segmenti processati in parallelo (PIPELINE_WORKERS) così la latenza di una
chiamata non blocca i segmenti successivi; i risultati vengono emessi in
ordine di inizio segmento.
"""
from __future__ import annotations

import logging
import queue
import random
import threading
import time
from typing import Callable

import numpy as np

from .. import config
from ..ai import gemini, service
from ..ai.errors import ProviderError


class _PausedSemaphore:
    """Semaphore che conta i segmenti in coda durante la pausa.
    Thread-safe su Windows (a differenza di queue.Queue.qsize()).
    """
    def __init__(self, max_segments: int):
        self._max = max_segments
        self._count = 0
        self._lk = threading.Lock()
        self._not_full = threading.Condition(self._lk)

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        with self._not_full:
            if self._count >= self._max:
                if not blocking:
                    return False
                if not self._not_full.wait(timeout):
                    return False
                if self._count >= self._max:    # slot occupato da altri
                    return False
            self._count += 1
            return True

    def release(self, n: int = 1) -> None:
        with self._not_full:
            self._count -= n
            if self._count < 0:
                self._count = 0
            self._not_full.notify_all()

    def __len__(self) -> int:
        with self._lk:
            return self._count


class Pipeline(threading.Thread):
    """Coda FIFO: segmento WAV -> provider AI -> callback con il risultato."""

    def __init__(
        self,
        settings: dict,
        on_result: Callable[[dict], None],
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_fault: Callable[[dict], None] | None = None,
    ):
        super().__init__(daemon=True, name="pipeline")
        self.settings = settings
        service.reset_session_language()
        self.on_result = on_result
        self.on_status = on_status or (lambda m: None)
        self.on_error = on_error or (lambda m: None)
        self.on_fault = on_fault or (lambda info: None)
        self.queue: "queue.Queue[dict | None]" = queue.Queue()
        self._paused_sem = _PausedSemaphore(config.MAX_PAUSED_SEGMENTS)
        self.bilingual = False
        self.calls = 0
        self.failures = 0
        self.faults: list[dict] = []
        self._resume = threading.Event()
        self._resume.set()
        self._fault_lk = threading.Lock()
        self._fault_notified = False
        self._last_call = 0.0
        self._seq = 0
        self._next_emit = 0
        self._pending: dict[int, dict] = {}
        self._emit_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stopping = threading.Event()
        self._abort = threading.Event()
        self.log = logging.getLogger("iacm.pipeline")

    @property
    def paused(self) -> bool:
        """True se la trascrizione è ferma per un errore in attesa di scelta."""
        return not self._resume.is_set()

    def submit(self, start_s: float, pcm: np.ndarray) -> None:
        if pcm.size == 0:
            return
        with self._emit_lock:
            seq = self._seq
            self._seq += 1
        if self.paused:
            acquired = self._paused_sem.acquire(blocking=False)
            if not acquired:
                self._emit(seq, {
                    "start": start_s,
                    "duration": pcm.size / config.TARGET_RATE,
                    "language": "",
                    "italian": "",
                    "original": "",
                    "bilingual": self.bilingual,
                    "error": "segmento scartato: coda piena durante la pausa "
                             "della trascrizione",
                })
                return
        else:
            acquired = True
        self.queue.put({
            "seq": seq,
            "start": start_s,
            "duration": pcm.size / config.TARGET_RATE,
            "pcm": pcm,
            "bilingual": self.bilingual,
            "_paused_acquired": acquired,
        })

    def resume(self, mode: str = "retry") -> int:
        """Riprende la trascrizione dopo un errore.

        mode="retry": riaccoda i segmenti fermati (stesso modello o quello
        appena cambiato nei settings).
        mode="skip":   li pubblica come errore e prosegue con i successivi.
        Ritorna il numero di segmenti gestiti.
        """
        with self._fault_lk:
            faults = list(self.faults)
            self.faults.clear()
            self._fault_notified = False
            self._resume.set()
        for item in faults:
            job = item["job"]
            if mode == "skip":
                self._emit(job["seq"], self._fail_result(
                    job, item["error"]))
            else:
                # Il finally del worker ha già rilasciato il semaforo quando
                # il job e' finito in faults: se lo lasciassimo a True qui
                # verrebbe rilasciato una seconda volta all'elaborazione.
                job["_paused_acquired"] = False
                self.queue.put(job)
        if faults:
            self.log.info("trascrizione ripresa (%s, %d segmenti)",
                          mode, len(faults))
            self.on_status("in ascolto…")
        return len(faults)

    def stop(self, drain: bool = True, timeout: float = 120.0) -> None:
        """Ferma la pipeline.

        Con drain (predefinito, quello che usa il pulsante FERMA) i segmenti
        gia' in coda vengono TRASCRITTI e TRADOTTI prima di chiudere: il
        lavoro gia' catturato non viene buttato. `_stopping` ferma solo la
        pausa per errori (nessun dialog a meta' chiusura); `_abort` annulla
        davvero ed e' riservato a drain=False o al timeout.
        """
        self._stopping.set()
        self._resume.set()
        with self._fault_lk:
            faults = list(self.faults)
            self.faults.clear()
            self._fault_notified = False
        for item in faults:
            self._emit(item["job"]["seq"],
                       self._fail_result(item["job"], item["error"]))
        if drain:
            deadline = time.monotonic() + timeout
            while self._pending_tasks() and time.monotonic() < deadline:
                time.sleep(0.1)
            n = self._pending_tasks()
            if n:
                self.log.warning(
                    "drain scaduto dopo %.0fs: %d segmenti non completati",
                    timeout, n)
                self._abort.set()
        else:
            self._abort.set()
        self._stop_event.set()
        self.join(timeout=timeout)

    def _pending_tasks(self) -> int:
        """Segmenti ancora in coda o in lavorazione (0 se tutto finito)."""
        try:
            return int(self.queue.unfinished_tasks)
        except Exception:                          # noqa: BLE001
            return 0

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        workers = max(1, int(config.PIPELINE_WORKERS))
        self.log.info("pipeline avviata (bilingual=%s, workers=%d)",
                      self.bilingual, workers)
        threads = [
            threading.Thread(target=self._worker, daemon=True,
                             name=f"pipeline-{i}")
            for i in range(workers)
        ]
        for t in threads:
            t.start()
        self._stop_event.wait()
        for _ in range(workers):
            self.queue.put(None)
        for t in threads:
            t.join(timeout=60)

    def _worker(self) -> None:
        while True:
            job = self.queue.get()
            try:
                if job is None:
                    return
                # in pausa per errore: il resto della coda resta in attesa
                # (la registrazione audio prosegue e continua a codificare)
                while not self._resume.is_set():
                    if self._stopping.is_set() or self._stop_event.is_set():
                        break
                    self._resume.wait(0.25)
                self._process(job)
            except Exception as exc:
                # Un errore non previsto non deve uccidere il worker: con
                # PIPELINE_WORKERS morti la coda resta piena e stop() si
                # blocca fino al timeout di queue.join().
                self.failures += 1
                self.log.exception("errore imprevisto nel worker")
                if job is not None:
                    try:
                        self._emit(
                            job["seq"],
                            self._fail_result(job, f"errore interno: {exc}"))
                    except Exception:
                        self.log.exception(
                            "impossibile pubblicare l'errore del worker")
            finally:
                if job is not None and job.get("_paused_acquired"):
                    self._paused_sem.release()
                self.queue.task_done()

    def _throttle(self) -> None:
        """Intervallo minimo globale tra l'inizio di due chiamate API.
        Solo per provider remoti (Gemini, OpenAI-compat); offline non ha rate limit.
        """
        if self.settings.get("stt", {}).get("type") == "offline":
            return
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < config.MIN_CALL_INTERVAL_S:
                time.sleep(config.MIN_CALL_INTERVAL_S - elapsed)
            self._last_call = time.monotonic()
            self.calls += 1

    def _emit(self, seq: int, result: dict) -> None:
        """Accoda fuori ordine e consegna in ordine di sequenza."""
        with self._emit_lock:
            self._pending[seq] = result
            while self._next_emit in self._pending:
                r = self._pending.pop(self._next_emit)
                self._next_emit += 1
                try:
                    self.on_result(r)
                except Exception as exc:
                    self.log.exception("errore callback risultato")
                    self.on_error(f"errore callback risultato: {exc}")

    @staticmethod
    def _fail_result(job: dict, error: str) -> dict:
        return {
            "start": job["start"],
            "duration": job["duration"],
            "language": "",
            "italian": "",
            "original": "",
            "bilingual": bool(job.get("bilingual")),
            "error": error or "trascrizione non riuscita",
        }

    def _fault(self, job: dict, error: str) -> None:
        """Errore definitivo: mette in pausa la trascrizione e avvisa l'UI."""
        notify = False
        with self._fault_lk:
            stopped = self._stopping.is_set()
            if stopped:
                self.failures += 1
            else:
                self.faults.append({"job": job, "error": error})
                self._resume.clear()
                if not self._fault_notified:
                    self._fault_notified = True
                    notify = True
        if stopped:
            self.log.warning("segmento t=%.1fs fallito durante la stop: %s",
                             job["start"], error)
            self._emit(job["seq"], self._fail_result(job, error))
            return
        self.failures += 1
        self.log.error("segmento t=%.1fs fallito: %s (trascrizione in pausa)",
                       job["start"], error)
        if notify:
            self.on_fault({
                "error": error,
                "model": (self.settings.get("stt") or {}).get("model", ""),
                "provider": (self.settings.get("stt") or {}).get("type", ""),
                "count": len(self.faults),
                "start": job["start"],
            })

    def _process(self, job: dict) -> None:
        wav = gemini.build_wav_bytes(job["pcm"].tobytes())
        bilingual = bool(job["bilingual"])
        result: dict | None = None
        last_err = ""
        fatal = False                     # errore non riprovabile: si ferma

        for attempt in range(config.MAX_RETRIES):
            if self._abort.is_set():
                self.log.info(
                    "segmento t=%.1fs annullato (ferma richiesta)",
                    job["start"])
                break
            self._throttle()
            try:
                parsed = service.transcribe_segment(
                    self.settings, wav,
                    duration=job["duration"], start=job["start"],
                    bilingual=bilingual,
                )
                result = {
                    "start": job["start"],
                    "duration": job["duration"],
                    "language": parsed.get("language", "it"),
                    "italian": parsed.get("italian", ""),
                    "original": parsed.get("original", "") if bilingual else "",
                    "bilingual": bilingual,
                    "error": None,
                }
                self.log.info(
                    "segmento t=%.1fs dur=%.1fs ok (tentativo %d, lingua=%s)",
                    job["start"], job["duration"], attempt + 1,
                    result["language"] or "?")
                break
            except ProviderError as exc:
                last_err = str(exc)
                fatal = not exc.retryable
                self.log.warning(
                    "segmento t=%.1fs tentativo %d/%d fallito: %s "
                    "(retryable=%s)", job["start"], attempt + 1,
                    config.MAX_RETRIES, last_err, exc.retryable)
                if fatal:
                    break
                delay = exc.retry_after
                if delay is None or delay > config.BACKOFF_MAX_S:
                    delay = min(config.BACKOFF_MAX_S,
                                config.BACKOFF_BASE_S * (2 ** attempt))
                delay += random.uniform(0, 1)
                self.on_status(f"ripresa automatica tra {delay:.0f}s ({last_err})")
                # durante la chiusura (drain) si continua a riprovare: si
                # interrompe solo con un abort esplicito o col timeout
                if self._abort.wait(delay):
                    break

        if result is None:
            if self._abort.is_set():
                # chiusura deliberata: niente dialog, esce come errore
                self.failures += 1
                result = self._fail_result(
                    job, last_err or "sessione interrotta prima della "
                                     "trascrizione")
            else:
                # errore definitivo o esauriti i retry: si ferma qui e si
                # chiede all'utente come proseguire (la registrazione no)
                self._fault(job, last_err)
                return
        self._emit(job["seq"], result)
