"""Dialog «Analisi file…»: carica un MP4/WAV/MP3 e genera il documento.

Serve per riassunto/minute in un secondo momento su una registrazione
salvata, o su qualunque file esterno: l'audio viene trascritto a blocchi
col provider di trascrizione attuale, poi gli stessi prompt delle
sessioni live generano il documento. Aggiornamenti UI via coda di
eventi (nessuna chiamata Tk dal thread di lavoro).
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import config
from ..ai import deferred as deferred_ai
from ..ai.errors import ProviderError
from .theme import BG, BLUE, FONT, FONT_SMALL, INK, MUTED, apply_style

log = logging.getLogger("iacm.ui.deferred")


class DeferredDialog(tk.Toplevel):
    """Scelta file + opzioni, poi pipeline in thread con progresso."""

    def __init__(self, parent, on_done):
        """``on_done(kind, text, stamp, out_dir)`` viene chiamato dalla
        finestra principale quando il documento e' pronto."""
        super().__init__(parent)
        self.title("Analisi file (audio/video) — IACallMinute")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=BG, padx=16, pady=14)
        apply_style(self)
        self.on_done = on_done
        self._cancel = threading.Event()
        self._running = False
        self._tmp_dir: Path | None = None
        self._q: "queue.Queue" = queue.Queue()

        tk.Label(self, text="Analisi differita di un file",
                 bg=BG, fg=INK, font=("Segoe UI Semibold", 13)).pack(
            anchor="w")
        tk.Label(self,
                 text="Genera riassunto o minute anche dopo la sessione "
                      "(o da audio/video esterni): l'audio viene "
                      "trascritto a blocchi e poi analizzato.",
                 bg=BG, fg=MUTED, font=FONT_SMALL,
                 wraplength=440, justify="left").pack(anchor="w",
                                                      pady=(2, 8))

        rf = tk.Frame(self, bg=BG)
        rf.pack(fill="x")
        tk.Label(rf, text="File:", bg=BG, fg=INK, font=FONT).pack(side="left")
        self.file_var = tk.StringVar()
        ttk.Entry(rf, textvariable=self.file_var, width=52).pack(
            side="left", padx=(6, 4))
        ttk.Button(rf, text="Sfoglia…", command=self._browse).pack(
            side="left")
        tk.Label(self,
                 text="Video: MP4/MKV/MOV/WebM/AVI · audio: "
                      "WAV/MP3/M4A/OGG/FLAC (per i formati compressi "
                      "serve ffmpeg nel PATH)",
                 bg=BG, fg=MUTED, font=FONT_SMALL, justify="left").pack(
            anchor="w", pady=(2, 10))

        kf = tk.Frame(self, bg=BG)
        kf.pack(fill="x")
        self.kind_var = tk.StringVar(value="summary")
        tk.Label(kf, text="Documento:", bg=BG, fg=INK, font=FONT).pack(
            side="left")
        ttk.Radiobutton(kf, text="Riassunto", variable=self.kind_var,
                        value="summary").pack(side="left", padx=(8, 4))
        ttk.Radiobutton(kf, text="Minute", variable=self.kind_var,
                        value="minutes").pack(side="left", padx=4)
        self.visual_var = tk.BooleanVar(value=False)
        self.visual_cb = ttk.Checkbutton(
            kf, text="Analizza anche i frame del video",
            variable=self.visual_var)
        self.visual_cb.pack(side="left", padx=(18, 0))
        tk.Label(self,
                 text="Analisi visiva dei frame (solo video): mostra che "
                      "cosa e' apparso a schermo, la piattaforma della "
                      "call e i parlanti evidenziati.",
                 bg=BG, fg=MUTED, font=FONT_SMALL, justify="left").pack(
            anchor="w", pady=(0, 10))

        # --- zona progresso (appare solo durante l'elaborazione) --- #
        self.pf = tk.Frame(self, bg=BG)
        self.bar = ttk.Progressbar(self.pf, maximum=1.0, length=460)
        self.bar.pack(anchor="w")
        self.bar.configure(value=0.0)
        self.msg_var = tk.StringVar(value="")
        tk.Label(self.pf, textvariable=self.msg_var, bg=BG, fg=BLUE,
                 font=FONT_SMALL, justify="left").pack(anchor="w",
                                                       pady=(4, 0))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", pady=(12, 0))
        self.run_btn = ttk.Button(btns, text="Avvia analisi",
                                  style="Accent.TButton",
                                  command=self._run)
        self.run_btn.pack(side="right")
        self.close_btn = ttk.Button(btns, text="Chiudi", command=self._close)
        self.close_btn.pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.grab_set()
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + max(0, (
                parent.winfo_width() - self.winfo_width()) // 2)
            y = parent.winfo_rooty() + max(0, (
                parent.winfo_height() - self.winfo_height()) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:                       # noqa: BLE001
            pass
        self.after(100, self._poll)

    # ------------------------------------------------------------------ #
    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Scegli il file da analizzare",
            initialdir=str(config.get_temp_root()),
            filetypes=[("Audio/video", "*.mp4 *.mkv *.mov *.webm *.avi "
                        "*.wav *.mp3 *.m4a *.ogg *.flac"),
                       ("Video", "*.mp4 *.mkv *.mov *.webm"),
                       ("Audio", "*.wav *.mp3 *.m4a *.ogg *.flac"),
                       ("Tutti i file", "*.*")])
        if path:
            self.file_var.set(path)

    def _poll(self) -> None:
        """Pump per gli aggiornamenti del thread di lavoro (coda eventi)."""
        try:
            while True:
                evt, payload = self._q.get_nowait()
                if evt == "prog":
                    self.bar.configure(value=max(0.0, min(1.0, payload[0])))
                    self.msg_var.set(payload[1])
                elif evt == "done":
                    self._finished(payload)
        except queue.Empty:
            pass
        except Exception as exc:                # noqa: BLE001
            log.exception("pump dialogo fallito: %s", exc)
        if self.winfo_exists():
            self.after(120, self._poll)

    def _run(self) -> None:
        if self._running:
            return
        src = Path(self.file_var.get().strip())
        if not src.is_file():
            messagebox.showinfo("File mancante",
                                "Scegli prima il file da analizzare.",
                                parent=self)
            return
        if not deferred_ai.is_supported(src):
            messagebox.showerror(
                "Formato non supportato",
                f"Estensione «{src.suffix or '?'}» non gestita: usa un "
                "video (MP4/MKV/MOV/WebM/AVI) o un audio "
                "(WAV/MP3/M4A/OGG/FLAC).", parent=self)
            return
        missing = config.missing_credentials()
        if missing:
            messagebox.showwarning(
                "Configurazione incompleta",
                "Mancano i dati per l'IA:\n\n• " + "\n• ".join(missing),
                parent=self)
            return
        self._running = True
        self._cancel.clear()
        self.run_btn.configure(state="disabled")
        self.pf.pack(fill="x", pady=(2, 0))
        self.bar.configure(value=0.0)
        self.msg_var.set("avvio…")
        kind = self.kind_var.get()
        visual = bool(self.visual_var.get()) and deferred_ai.is_video(src)
        settings = dict(config.SETTINGS)
        stamp = deferred_ai.source_stamp(src)
        out_dir = (config.SETTINGS.get("output_dir")
                   or str(Path.home() / "Documents" / "IACallMinute"))
        # nota: NON inizia con "session_"/"selftest_" perche' la pulizia
        # all'avvio (purge_temp_sessions) eliminerebbe l'analisi in corso
        self._tmp_dir = config.get_temp_root() / f"deferred_{stamp}"
        try:
            self._tmp_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:                # noqa: BLE001
            log.warning("cartella temp per l'analisi: %s", exc)
            self._tmp_dir = None

        def rep(frac: float, msg: str) -> None:
            self._q.put(("prog", (frac, msg)))

        def worker() -> None:
            res: dict = {"ok": False}
            try:
                data = deferred_ai.transcribe_file(
                    settings, src, self._tmp_dir or config.get_temp_root(),
                    visual=visual, on_progress=rep, cancel=self._cancel)
                rep(0.85, "generazione del documento con l'IA…")
                text = deferred_ai.generate_document(
                    settings, kind, data, src, context_text="",
                    on_status=lambda s: rep(0.85, s))
                rep(1.0, "documento pronto")
                res = {"ok": True, "text": text, "kind": kind,
                       "stamp": stamp, "out_dir": out_dir}
            except ProviderError as exc:
                res = {"ok": False, "error": str(exc),
                       "cancelled": "annullata" in str(exc)}
            except Exception as exc:            # noqa: BLE001
                log.exception("analisi differita fallita")
                res = {"ok": False, "error": str(exc)}
            self._q.put(("done", res))

        threading.Thread(target=worker, daemon=True,
                         name=f"deferred-{kind}").start()

    def _finished(self, res: dict) -> None:
        self._running = False
        self.run_btn.configure(state="normal")
        if res.get("ok"):
            log.info("analisi differita completata (%d car.)",
                     len(res["text"]))
            self.msg_var.set("documento pronto: si apre l'anteprima…")
            self.after(600, lambda: self._hand_off(res))
            return
        self.msg_var.set("")
        self.pf.pack_forget()
        if res.get("cancelled"):
            messagebox.showinfo("Analisi annullata",
                                "Nessun documento generato.", parent=self)
        else:
            messagebox.showerror("Analisi non riuscita",
                                 res.get("error") or "errore sconosciuto",
                                 parent=self)

    def _hand_off(self, res: dict) -> None:
        """Passa il documento alla finestra principale e chiude il dialogo."""
        try:
            self.on_done(res["kind"], res["text"], res["stamp"],
                         res["out_dir"])
            self._close()
        except Exception as exc:                # noqa: BLE001
            log.exception("passaggio risultato fallito")
            messagebox.showerror("Errore interno", str(exc), parent=self)

    def _close(self) -> None:
        if self._running:
            if not messagebox.askyesno(
                    "Analisi in corso",
                    "L'analisi è ancora in esecuzione. Annullarla e "
                    "chiudere?", parent=self):
                return
        self._cancel.set()
        try:
            if self._tmp_dir:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except Exception:                       # noqa: BLE001
            pass
        self.destroy()
