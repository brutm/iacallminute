"""Interfaccia grafica di IACallMinute (Tkinter, tema moderno e leggero)."""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import autostop, callwatch, config, power
from ..ai import context as context_ai
from ..ai import deferred as deferred_ai
from ..ai import prompts, service
from ..audio.capture import AudioSession, list_audio_devices
from ..storage import outputs, recorder, screenrec
from ..transcription.pipeline import Pipeline
from .deferred import DeferredDialog
from .hwmon import HwMon
from .mdtext import insert_markdown, setup_tags as setup_md_tags
from .progress import SaveProgressDialog
from .screen import ScreenDialog
from .settings import SettingsDialog
from .tkutil import SafeDestroy, ui_scale
from .theme import (AMBER, BG, BLUE, BORDER, CARD, FONT, FONT_B, FONT_HEADER,
                    FONT_MONO, FONT_SMALL, GREEN, HEADER, INK, MUTED, RED,
                    TRACK, apply_style)

# Nome del documento/file per tipo di output generato (default: riassunto).
DOC_STEM = {"minutes": "minute", "video": "analisi_video"}

# --------------------------------------------------------------------------- #
# Dialog contesto (prima della generazione delle minute)
# --------------------------------------------------------------------------- #
class ContextDialog(tk.Toplevel):
    """Contesto della chiamata: note libere + documenti/cartella progetto.

    Finestra singola: se già aperta viene solo messa in primo piano.
    """

    def __init__(self, parent: tk.Misc, notes: str = "", folder: str = ""):
        super().__init__(parent)
        self.title("Contesto della chiamata")
        self.transient(parent)
        self.resizable(True, False)
        self.configure(bg=BG)
        self.result: dict | None = None

        outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, padx=16, pady=14)
        outer.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        tk.Label(outer,
                 text="Contesto — scrivilo PRIMA (o durante) la call, così "
                      "l'IA capisce di cosa si parla",
                 bg=CARD, fg=BLUE, font=("Segoe UI Semibold", 11)).grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.notes = tk.Text(outer, width=72, height=5, wrap="word", font=FONT,
                             relief="solid", borderwidth=1,
                             highlightbackground=BORDER,
                             highlightcolor=BORDER, highlightthickness=1,
                             insertbackground=INK)
        self.notes.grid(row=1, column=0, columnspan=3, sticky="nsew",
                        pady=(4, 2))
        tk.Label(outer,
                 text="Esempio: «call tecnica sul modulo di fatturazione di "
                      "MyApp, discutiamo il nuovo API gateway»",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 12))

        tk.Label(outer, text="Documenti / cartella da far analizzare all'IA "
                             "(facoltativo)",
                 bg=CARD, fg=BLUE, font=("Segoe UI Semibold", 11)).grid(
            row=3, column=0, columnspan=3, sticky="w")
        self.folder_var = tk.StringVar(value=folder)
        ttk.Entry(outer, textvariable=self.folder_var).grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=(0, 6), pady=(4, 2))
        ttk.Button(outer, text="Sfoglia…", command=self._browse).grid(
            row=4, column=2, pady=(4, 2))
        tk.Label(outer,
                 text="Esempio: la cartella del progetto di cui parli nel "
                      "contesto — l'IA ne legge README e struttura.",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9), justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(2, 14))

        btns = ttk.Frame(outer, style="Card.TFrame")
        btns.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(btns, text="Annulla", command=self.destroy).grid(
            row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Salva contesto", style="Accent.TButton",
                   command=self._ok).grid(row=0, column=1)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.notes.insert("1.0", notes)
        self.notes.focus_set()

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self,
                                         title="Seleziona cartella progetto")
        if chosen:
            self.folder_var.set(chosen)

    def _ok(self) -> None:
        self.result = {
            "notes": self.notes.get("1.0", "end").strip(),
            "folder": self.folder_var.get().strip() or None,
        }
        self.destroy()


class FaultDialog(tk.Toplevel):
    """Avviso di errore della trascrizione: NON blocca la registrazione.

    Finestra non modale: l'audio continua a registrare e i segmenti restano
    in coda. L'utente sceglie se riprovare con lo stesso modello, cambiarlo
    o saltare i segmenti fermati.
    """

    def __init__(self, parent: tk.Misc, info: dict, models: list[str],
                 current: str, on_action):
        super().__init__(parent)
        self.title("Trascrizione in pausa")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(parent)
        self.on_action = on_action
        self.protocol("WM_DELETE_WINDOW", self._hide)

        outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, padx=16, pady=14)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        tk.Label(outer, text="⚠  Trascrizione in pausa", bg=CARD, fg=RED,
                 font=("Segoe UI Semibold", 12)).grid(
            row=0, column=0, sticky="w")
        tk.Label(outer,
                 text="La registrazione AUDIO continua: nessun segmento va "
                      "perso, resta solo in attesa di essere trascritto.",
                 bg=CARD, fg=MUTED, font=FONT_SMALL, justify="left",
                 wraplength=460).grid(row=1, column=0, sticky="w", pady=(4, 8))

        self.err_lbl = tk.Label(outer, text=str(info.get("error", "")),
                                bg="#fef2f2", fg=RED, font=FONT_MONO,
                                justify="left", anchor="w", wraplength=460,
                                padx=8, pady=6, relief="solid", borderwidth=1)
        self.err_lbl.grid(row=2, column=0, sticky="ew")

        self.meta_lbl = tk.Label(outer, text="", bg=CARD, fg=MUTED,
                                 font=FONT_SMALL, justify="left",
                                 wraplength=460)
        self.meta_lbl.grid(row=3, column=0, sticky="w", pady=(6, 10))
        self._set_meta(info)

        tk.Label(outer, text="Modello da usare per la ripresa", bg=CARD,
                 fg=MUTED, font=FONT_SMALL).grid(
            row=4, column=0, sticky="w")
        self.model_var = tk.StringVar(value=current)
        self.model_cb = ttk.Combobox(outer, textvariable=self.model_var,
                                     state="readonly",
                                     values=[current] + [m for m in models
                                                         if m != current])
        self.model_cb.grid(row=5, column=0, sticky="ew", pady=(2, 0))

        btns = tk.Frame(outer, bg=CARD)
        btns.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        btns.columnconfigure(0, weight=1)
        self.retry_btn = ttk.Button(
            btns, text="▶  Riprova con questo modello",
            style="Accent.TButton",
            command=lambda: self._act("retry"))
        self.retry_btn.grid(row=0, column=0, columnspan=2, sticky="ew",
                            ipady=2)
        ttk.Button(btns, text="Salta i segmenti fermati",
                   command=lambda: self._act("skip")).grid(
            row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        ttk.Button(btns, text="⚙  Configurazione…",
                   command=lambda: self._act("config")).grid(
            row=1, column=1, sticky="ew", pady=(6, 0), padx=(4, 0))
        ttk.Button(btns, text="Chiudi (resta in pausa)",
                   command=self._hide).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.geometry("+%d+%d" % (
            parent.winfo_rootx() + parent.winfo_width() // 4,
            parent.winfo_rooty() + parent.winfo_height() // 4))

    def _set_meta(self, info: dict) -> None:
        model = str(info.get("model") or "?")
        typ = str(info.get("provider") or "?")
        n = int(info.get("count") or 1)
        self.meta_lbl.configure(
            text=f"modello attuale: {model} ({typ}) · "
                 f"segmenti fermati in attesa: {n}")

    def update_info(self, info: dict) -> None:
        self.err_lbl.configure(text=str(info.get("error", "")))
        self._set_meta(info)
        if str(self.retry_btn.cget("text")).endswith("…"):
            self.retry_btn.configure(state="normal",
                                     text="▶  Riprova con questo modello")

    def set_models(self, models: list[str], current: str) -> None:
        if not models:
            return
        if current not in models:
            models = [current] + list(models)
        self.model_cb.configure(values=models)
        if not self.model_var.get():
            self.model_var.set(current)

    def _act(self, action: str) -> None:
        model = self.model_var.get().strip()
        if action == "retry":
            self.retry_btn.configure(state="disabled", text="ripresa…")
        self.on_action(action, model)
        if action != "config":
            self.destroy()

    def _hide(self) -> None:
        self.withdraw()

    def reopen(self) -> None:
        self.deiconify()
        self.lift()


class ResultViewer(tk.Toplevel):
    """Finestra laterale: mostra il risultato generato e «Salva…» a scelta."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        # Figlia della principale: su Windows resta sopra di lei e non
        # finisce dietro al momento del click sul pulsante.
        self.transient(parent)
        self.configure(bg=CARD)
        s = ui_scale(self)
        self.geometry(f"{int(560 * s)}x{int(680 * s)}")
        self.minsize(int(420 * s), int(360 * s))
        self.title("Risultato")
        self.kind = "summary"
        self.stamp = ""
        self.output_dir = "C:/Temp/IACallMinute"
        self.content: str | None = None
        self.log = logging.getLogger("iacm.ui.viewer")

        head = tk.Frame(self, bg=CARD, padx=12, pady=8)
        head.pack(fill="x")
        head.columnconfigure(0, weight=1)
        self.title_lbl = tk.Label(head, text="Risultato", bg=CARD, fg=BLUE,
                                  font=("Segoe UI Semibold", 12))
        self.title_lbl.grid(row=0, column=0, sticky="w")
        self.status_lbl = tk.Label(head, text="", bg=CARD, fg=MUTED,
                                   font=FONT_SMALL)
        self.status_lbl.grid(row=1, column=0, sticky="w", pady=(2, 0))

        body = tk.Frame(self, bg=CARD, padx=12)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.text = tk.Text(body, wrap="word", font=("Segoe UI", 11),
                            state="disabled", relief="solid", borderwidth=1,
                            highlightbackground=BORDER,
                            highlightcolor=BORDER, highlightthickness=1,
                            padx=10, pady=8, bg="white", fg=INK,
                            insertbackground=BLUE, selectbackground="#bfdbfe")
        scroll = ttk.Scrollbar(body, orient="vertical",
                               command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.tag_configure("err", foreground=RED)
        setup_md_tags(self.text)

        btns = tk.Frame(self, bg=CARD, padx=12, pady=10)
        btns.pack(fill="x")
        self.save_btn = ttk.Button(btns, text="Salva…",
                                   style="Accent.TButton",
                                   command=self._save, state="disabled")
        self.save_btn.pack(side="right")
        ttk.Button(btns, text="Chiudi", command=self.destroy).pack(
            side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def prepare(self, kind: str, stamp: str, output_dir: str,
                title: str) -> None:
        self.kind = kind
        self.stamp = stamp
        self.output_dir = output_dir or "C:/Temp/IACallMinute"
        self.content = None
        self.title(title)
        self.title_lbl.configure(text=f"{title} — anteprima")
        self.status_lbl.configure(text="generazione in corso…", fg=AMBER)
        self.save_btn.configure(state="disabled")
        self._set_text("generazione in corso…\n")

    def set_content(self, text: str) -> None:
        self.content = text
        self._set_text(text, md=True)
        self.status_lbl.configure(
            text="pronto — «Salva…» per scegliere cartella e nome", fg=GREEN)
        self.save_btn.configure(state="normal")
        self.log.info("viewer: contenuto pronto (%d car)", len(text))

    def set_error(self, msg: str) -> None:
        self.content = None
        self._set_text(f"Generazione non riuscita:\n{msg}\n", tag="err")
        self.status_lbl.configure(text="errore", fg=RED)
        self.save_btn.configure(state="disabled")

    def _set_text(self, content: str, tag: str | None = None,
                  md: bool = False) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if md:
            insert_markdown(self.text, content)
        else:
            self.text.insert("end", content, tag)
        self.text.configure(state="disabled")
        self.text.see("1.0")

    def _save(self) -> None:
        if not self.content:
            return
        stem = DOC_STEM.get(self.kind, "riassunto")
        initial = f"{stem}_{self.stamp}.md" if self.stamp else f"{stem}.md"
        path = filedialog.asksaveasfilename(
            parent=self, title="Salva risultato", defaultextension=".md",
            initialdir=self.output_dir, initialfile=initial,
            filetypes=[("Markdown", "*.md"), ("Tutti i file", "*.*")])
        if not path:
            return
        try:
            paths = [outputs.write_text(Path(path), self.content)]
            if self.kind == "minutes":
                paths.append(outputs.markdown_to_docx(
                    self.content, Path(path).with_suffix(".docx")))
            self.status_lbl.configure(text="salvato", fg=GREEN)
            self.log.info("viewer: salvato %s", paths)
            messagebox.showinfo(
                "Salvato", "File:\n\n" + "\n".join(str(x) for x in paths),
                parent=self)
        except Exception as exc:
            self.log.exception("viewer: salvataggio fallito")
            messagebox.showerror("Salvataggio non riuscito", str(exc),
                                 parent=self)

    def present(self) -> None:
        """Porta la finestra in primo piano, anche alla prima apertura."""
        if not self.winfo_exists():
            return
        self.update_idletasks()
        self.reposition()
        try:
            self.deiconify()
        except Exception:
            pass
        self.lift()
        self.focus_force()
        # Al primo apertura il map avviene solo al ritorno all'event loop e
        # Windows può rimettere la principale davanti: ripasso dopo un istante.
        self.after(180, self._restack)

    def _restack(self) -> None:
        if not self.winfo_exists():
            return
        try:
            if self.state() == "iconic":
                self.deiconify()
        except Exception:
            pass
        self.lift()
        self.focus_force()

    def reposition(self) -> None:
        """Si pone a destra della finestra principale (o a sinistra se serve)."""
        parent = self.master
        try:
            self.update_idletasks()
            # Prima del map winfo_width() == 1: con la principale a schermo
            # intero la finestra veniva spinta fuori schermo al primo click.
            w = self.winfo_width()
            if not self.winfo_ismapped() or w <= 1:
                w = self.winfo_reqwidth()
            x = parent.winfo_rootx() + parent.winfo_width()
            y = parent.winfo_rooty()
            if x + w > self.winfo_screenwidth():
                x = parent.winfo_rootx() - w
            if x < 0:
                x = max(0, self.winfo_screenwidth() - w)
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Finestra principale
# --------------------------------------------------------------------------- #
class App(SafeDestroy, tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        apply_style(self)
        self._base_title = f"IACallMinute {config.APP_VERSION}"
        self.title(self._base_title)
        # misure in pixel scalate con i DPI (testo nitido a 125/150%)
        self._scale = s = ui_scale(self)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(1100 * s), max(640, sw - 40))
        h = min(int(780 * s), max(480, sh - 80))
        self.geometry(f"{w}x{h}")
        self.minsize(min(int(820 * s), w), min(int(580 * s), h))

        # stato
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.state_ = "idle"           # idle | running | stopping | stopped
        self.generating = False
        self.results: list[dict] = []
        self.audio: AudioSession | None = None
        self.pipeline: Pipeline | None = None
        self.logger = logging.getLogger("iacm.ui")
        self.session_dir = config.new_session_dir()
        self.stamp = time.strftime("%Y%m%d_%H%M%S")
        self.started_mono: float | None = None
        self.final_duration = 0.0
        self._wav_temp: Path | None = None
        self._screen_rec = None
        self._video_temp: str | None = None
        self.video_analysis: str | None = None
        self._destroy_after_stop = False
        self._last_level_update = 0.0
        self._seg_count = 0
        self._sess_peak = 0.0
        self._sess_devices = ""
        self._reset_volume_check()
        self.viewer: ResultViewer | None = None
        self._context_dlg: ContextDialog | None = None
        self._fault_dlg: FaultDialog | None = None
        self.fault_info: dict | None = None
        self._save_dlg: SaveProgressDialog | None = None
        self._save_cancel: threading.Event | None = None
        self._deferred_dlg = None
        self._lang_mismatch = 0
        # arresto automatico (timer): scadenza in time.monotonic()
        self._autostop_deadline: float | None = None
        self._autostop_warned = False
        self._autostop_fired = False
        self._autostop_reason = "timer"     # timer | fine chiamata | fermata manuale
        self._session_saved = False         # almeno un salvataggio riuscito
        # arresto a fine chiamata (microfono rilasciato da Teams & co.)
        self._callwatch: callwatch.CallWatch | None = None
        self._micprobe: callwatch.MicProbe | None = None
        self._callwatch_next = 0.0
        self._callwatch_warned = False
        self._var_cache: dict[str, str] = {}
        self._pill_cache: tuple[str, str] | None = None
        self._level_style = ""

        # writer di transcript_live.md: scrive al massimo ogni ~2 s
        # l'ultima istantanea dei risultati (niente copia della lista e
        # niente riscrittura completa del file a ogni segmento)
        self._tx_dirty = threading.Event()
        self._tx_write_stop = threading.Event()
        self._tx_write_thread = threading.Thread(
            target=self._tx_write_worker, daemon=True, name="tx-writer")
        self._tx_write_thread.start()

        # variabili (default dai settings persistenti)
        self.out_dir_var = tk.StringVar(
            value=config.SETTINGS.get("output_dir")
            or str(Path.home() / "Documents" / "IACallMinute"))
        self.temp_dir_var = tk.StringVar(value=str(config.get_temp_root()))
        self.bilingual_var = tk.BooleanVar(
            value=bool(config.SETTINGS.get("bilingual", True)))
        self.video_var = tk.BooleanVar(
            value=bool(config.SETTINGS.get("record_video", False)))
        self.lb_var = tk.StringVar()
        self.mic_var = tk.StringVar()
        self.status_var = tk.StringVar(value="pronto")
        self._status_cache = "pronto"          # vedi _set_status: set solo al cambio
        self.dur_var = tk.StringVar(value="00:00")
        self.seg_var = tk.StringVar(value="segmenti 0")
        self.api_var = tk.StringVar(value="API 0")
        self.level_var = tk.DoubleVar(value=0)
        self.hw_var = tk.StringVar(value="")
        self.dev_var = tk.StringVar(value="")
        self.timer_var = tk.StringVar(value="")
        auto = config.SETTINGS.get("auto_stop") or {}
        self.autostop_mode_var = tk.StringVar(
            value=autostop.MODE_LABELS["off"])
        self.autostop_min_var = tk.StringVar(
            value=str(auto.get("minutes") or 60))
        self.autostop_at_var = tk.StringVar(
            value=str(auto.get("at") or "18:00"))
        self.autostop_desc_var = tk.StringVar(value="")
        # come la modalità del timer: vale per la sessione, riparte spento
        self.callend_var = tk.BooleanVar(value=False)
        self.autosave_tx_var = tk.BooleanVar(
            value=bool(auto.get("save_transcript", True)))
        self.autosave_sum_var = tk.BooleanVar(
            value=bool(auto.get("save_summary", False)))
        self.autosave_min_var = tk.BooleanVar(
            value=bool(auto.get("save_minutes", False)))

        self._loopbacks: list[dict] = []
        self._mics: list[dict] = []

        self._build_widgets()
        self._load_devices()
        self._update_provider_label()
        self._update_controls()
        self.logger.info("UI pronta v%s", config.APP_VERSION)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_self_configure, add="+")
        self._poll_after = self.after(100, self._poll)

    # ------------------------------------------------------------------ #
    # Costruzione widget
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        # --- header ---
        header = tk.Frame(self, bg=HEADER, height=int(64 * self._scale))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(5, weight=1)
        tk.Label(header, text="● IACallMinute", bg=HEADER, fg="#38bdf8",
                 font=FONT_HEADER).grid(row=0, column=0, sticky="w",
                                        padx=(16, 6), pady=14)
        tk.Label(header,
                 text="trascrizione live di Teams / Zoom con AI",
                 bg=HEADER, fg="#94a3b8", font=FONT_SMALL).grid(
            row=0, column=1, sticky="w", pady=16)
        self.pill = tk.Label(header, text=" PRONTO ", bg="#475569",
                             fg="white", font=("Segoe UI Semibold", 9),
                             padx=10, pady=3)
        self.pill.grid(row=0, column=6, padx=(0, 16))
        self.header_model = tk.Label(
            header, text="modello: —", bg=HEADER,
            fg="#64748b", font=FONT_MONO)
        self.header_model.grid(row=0, column=7, padx=(0, 12))

        # --- card configurazione ---
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1, padx=14, pady=12)
        card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14,
                  pady=(12, 6))
        card.columnconfigure(1, weight=1)

        tk.Label(card, text="Cartella destinazione", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, sticky="w")
        self.out_entry = ttk.Entry(card, textvariable=self.out_dir_var)
        self.out_entry.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=(0, 8), pady=(2, 0))
        self.browse_btn = ttk.Button(card, text="Sfoglia…",
                                     command=self._browse_out)
        self.browse_btn.grid(row=1, column=2, pady=(2, 0))

        tk.Label(card, text="Audio sistema", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Label(card, text="Microfono", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=2, column=1, sticky="w",
                                       padx=(8, 0), pady=(10, 0))
        self.lb_combo = ttk.Combobox(card, textvariable=self.lb_var,
                                     state="readonly")
        self.lb_combo.grid(row=3, column=0, sticky="ew", padx=(0, 8),
                           pady=(2, 0))
        self.mic_combo = ttk.Combobox(card, textvariable=self.mic_var,
                                      state="readonly")
        self.mic_combo.grid(row=3, column=1, sticky="ew", padx=(0, 8),
                            pady=(2, 0))
        self.refresh_btn = ttk.Button(card, text="Aggiorna",
                                      command=self._load_devices)
        self.refresh_btn.grid(row=3, column=2, pady=(2, 0))

        tk.Label(card, text="temporanea", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.provider_label = tk.Label(card, text="", bg=CARD, fg=GREEN,
                                       font=FONT_MONO, anchor="e")
        self.provider_label.grid(row=4, column=1, sticky="e",
                                 pady=(10, 0), padx=(8, 0))
        self.config_btn = ttk.Button(card, text="\u2699  Configurazione…",
                                     command=self._open_settings)
        self.config_btn.grid(row=4, column=2, sticky="e", pady=(10, 0))
        self.temp_entry = ttk.Entry(card, textvariable=self.temp_dir_var)
        self.temp_entry.grid(row=5, column=0, columnspan=2, sticky="ew",
                             padx=(0, 8), pady=(4, 0))
        self.temp_browse_btn = ttk.Button(card, text="Sfoglia…",
                                          command=self._browse_temp)
        self.temp_browse_btn.grid(row=5, column=2, pady=(4, 0))

        self.video_cb = ttk.Checkbutton(
            card, text="Con video (registra lo schermo, MP4 con audio)",
            variable=self.video_var, command=self._on_video_toggle)
        self.video_cb.grid(row=6, column=0, columnspan=2, sticky="w",
                           pady=(12, 0))
        self.screen_area_lbl = tk.Label(card, text="", bg=CARD, fg=MUTED,
                                        font=FONT_SMALL, anchor="w")
        self.screen_area_lbl.grid(row=7, column=0, columnspan=2,
                                  sticky="ew", pady=(6, 0))
        self.area_btn = ttk.Button(card, text="Area…",
                                   command=self._on_screen)
        self.area_btn.grid(row=7, column=2, pady=(6, 0))
        self._on_video_toggle()

        # --- arresto automatico (timer) ---
        trow = tk.Frame(card, bg=CARD)
        trow.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        tk.Label(trow, text="\u23f1  Arresto automatico", bg=CARD, fg=INK,
                 font=FONT_B).pack(side="left")
        self.autostop_combo = ttk.Combobox(
            trow, textvariable=self.autostop_mode_var, state="readonly",
            width=17, values=[lbl for _, lbl in autostop.MODES])
        self.autostop_combo.pack(side="left", padx=(10, 6))
        self.autostop_combo.bind("<<ComboboxSelected>>",
                                 lambda e: self._on_autostop_change())
        self._autostop_val = tk.Frame(trow, bg=CARD)
        self._autostop_val.pack(side="left")
        self.autostop_min_sb = ttk.Spinbox(
            self._autostop_val, textvariable=self.autostop_min_var, width=6,
            values=(5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360),
            command=self._on_autostop_change)
        self.autostop_min_lbl = tk.Label(self._autostop_val, text="min",
                                         bg=CARD, fg=MUTED, font=FONT_SMALL)
        self.autostop_at_entry = ttk.Entry(
            self._autostop_val, textvariable=self.autostop_at_var, width=7,
            justify="center")
        for w in (self.autostop_min_sb, self.autostop_at_entry):
            w.bind("<Return>", lambda e: self._on_autostop_change())
            w.bind("<FocusOut>", lambda e: self._on_autostop_change())
        self.autostop_desc_lbl = tk.Label(
            trow, textvariable=self.autostop_desc_var, bg=CARD, fg=MUTED,
            font=FONT_SMALL, anchor="w")
        self.autostop_desc_lbl.pack(side="left", padx=(10, 0), fill="x",
                                    expand=True)
        # descrizione aggiornata mentre si scrive; la scadenza invece si
        # applica solo a conferma (Invio, frecce, uscita dal campo)
        for var in (self.autostop_min_var, self.autostop_at_var):
            var.trace_add("write", lambda *_: self._refresh_autostop_desc())

        # arresto a fine chiamata (Teams, Zoom, Meet …)
        crow = tk.Frame(card, bg=CARD)
        crow.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(
            crow, text="\U0001f4de  Ferma a fine chiamata",
            variable=self.callend_var,
            command=self._on_callend_toggle).pack(side="left")
        tk.Label(crow, text="Teams, Zoom, Meet…: quando l'app rilascia il "
                 "microfono (o dopo 5 min di silenzio)", bg=CARD, fg=MUTED,
                 font=FONT_SMALL, anchor="w").pack(
            side="left", padx=(10, 0), fill="x", expand=True)

        # documenti da salvare da soli all'arresto automatico (visibile solo
        # con timer o fine chiamata attivi)
        self.autosave_row = tk.Frame(card, bg=CARD)
        self.autosave_row.grid(row=10, column=0, columnspan=3, sticky="ew",
                               pady=(6, 0))
        tk.Label(self.autosave_row, text="all'arresto automatico salva anche:",
                 bg=CARD, fg=MUTED, font=FONT_SMALL).pack(side="left")
        for text, var in (("Trascrizione", self.autosave_tx_var),
                          ("Riassunto", self.autosave_sum_var),
                          ("Minute", self.autosave_min_var)):
            ttk.Checkbutton(self.autosave_row, text=text, variable=var,
                            command=self._persist_autostop).pack(
                side="left", padx=(10, 0))
        self._sync_autostop_widgets()

        self.start_btn = ttk.Button(
            card, text="▶   AVVIA REGISTRAZIONE", style="Success.TButton",
            command=self._on_start)
        self.start_btn.grid(row=11, column=0, columnspan=3, sticky="ew",
                            pady=(14, 0), ipady=2)

        # --- barra comandi (due righe: niente pulsanti tagliati) ---
        bar = tk.Frame(self, bg=BG)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14,
                 pady=(4, 2))
        line1 = tk.Frame(bar, bg=BG)
        line1.pack(fill="x")
        line2 = tk.Frame(bar, bg=BG)
        line2.pack(fill="x", pady=(6, 0))

        self.stop_btn = ttk.Button(line1, text="■   FERMA",
                                   style="Danger.TButton",
                                   command=self._on_stop)
        self.stop_btn.pack(side="left", padx=(0, 10))
        self.tr_live_cb = ttk.Checkbutton(
            line1, text="Traduzione live", style="Bg.TCheckbutton",
            variable=self.bilingual_var, command=self._toggle_translation)
        self.tr_live_cb.pack(side="left")
        self.context_btn = ttk.Button(line1, text="Contesto",
                                      style="Accent.TButton",
                                      command=self._on_context)
        self.context_btn.pack(side="right")
        self.deferred_btn = ttk.Button(line1, text="Analisi file…",
                                       command=self._on_deferred)
        self.deferred_btn.pack(side="right", padx=(0, 8))

        self.tx_btn = ttk.Button(line2, text="Salva trascrizione…",
                                 command=self._on_save_tx)
        self.tx_btn.pack(side="left", padx=(0, 4))
        self.rec_btn = ttk.Button(line2, text="Salva registrazione…",
                                  command=self._on_save_rec)
        self.rec_btn.pack(side="left", padx=4)
        self.video_btn = ttk.Button(line2, text="Salva video…",
                                    command=self._on_save_video)
        self.video_btn.pack(side="left", padx=4)
        ttk.Separator(line2, orient="vertical").pack(
            side="left", fill="y", padx=10, pady=2)
        self.summary_btn = ttk.Button(line2, text="Riassunto",
                                      command=self._on_summary)
        self.summary_btn.pack(side="left", padx=4)
        self.minutes_btn = ttk.Button(line2, text="Minute",
                                      command=self._on_minutes)
        self.minutes_btn.pack(side="left", padx=4)
        self.analysis_btn = ttk.Button(line2, text="Analisi video…",
                                       command=self._on_video_analysis)
        self.analysis_btn.pack(side="left", padx=4)

        # --- trascrizione live ---
        body = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        body.grid(row=3, column=0, sticky="nsew", padx=(14, 4), pady=(4, 4))
        self.rowconfigure(3, weight=1)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        # --- sidebar: contesto attualmente salvato (evidenza) ---
        ctx = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1)
        ctx.grid(row=3, column=1, sticky="nsew", padx=(4, 14), pady=(4, 4))
        ctx.rowconfigure(1, weight=1)
        ctx.columnconfigure(0, weight=1)
        ch = tk.Frame(ctx, bg=CARD)
        ch.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10,
                pady=(8, 4))
        tk.Label(ch, text="CONTESTO ATTIVO", bg=CARD, fg=BLUE,
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Button(ch, text="Modifica…", command=self._on_context).pack(
            side="right")
        self.ctx_text = tk.Text(
            ctx, wrap="word", state="disabled", font=("Segoe UI", 10),
            bg="#f8fafc", fg=INK, relief="flat", padx=10, pady=8,
            highlightbackground=BORDER, highlightthickness=1,
            highlightcolor=BORDER, spacing1=2, spacing3=4)
        self.ctx_text.tag_configure(
            "ph", foreground=MUTED, font=("Segoe UI", 10, "italic"))
        self.ctx_text.grid(row=1, column=0, sticky="nsew", padx=(10, 0),
                           pady=(0, 4))
        cscroll = ttk.Scrollbar(ctx, orient="vertical",
                                command=self.ctx_text.yview)
        self.ctx_text.configure(yscrollcommand=cscroll.set)
        cscroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 4))
        self.ctx_folder_lbl = tk.Label(
            ctx, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9),
            wraplength=int(270 * self._scale), justify="left", anchor="w",
            padx=10)
        self.ctx_folder_lbl.grid(row=2, column=0, columnspan=2, sticky="ew",
                                 padx=10, pady=(4, 8))

        self.text = tk.Text(
            body, wrap="word", state="disabled", font=("Segoe UI", 11),
            padx=14, pady=12, spacing1=3, spacing3=6, bg="white", fg=INK,
            relief="flat", highlightthickness=0, bd=0,
            insertbackground=BLUE, selectbackground="#bfdbfe")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self.text.tag_configure("ts", foreground=BLUE,
                                font=("Segoe UI Semibold", 10))
        self.text.tag_configure("orig", foreground="#94a3b8",
                                lmargin1=18, lmargin2=18,
                                font=("Segoe UI", 10))
        self.text.tag_configure("it", foreground=INK, lmargin1=18, lmargin2=18)
        self.text.tag_configure("err", foreground=RED)
        self.text.tag_configure("sys", foreground=GREEN,
                                font=("Segoe UI", 9, "italic"))

        # --- barra di stato ---
        sb = tk.Frame(self, bg="#e8edf3", height=int(34 * self._scale))
        sb.grid(row=4, column=0, columnspan=2, sticky="ew")
        sb.grid_propagate(False)
        sb.columnconfigure(10, weight=1)
        tk.Label(sb, textvariable=self.status_var, bg="#e8edf3", fg="#334155",
                 font=FONT_SMALL).grid(row=0, column=0, sticky="w", padx=(12, 0))
        tk.Label(sb, textvariable=self.timer_var, bg="#e8edf3", fg=AMBER,
                 font=("Consolas", 9, "bold")).grid(
            row=0, column=10, sticky="e", padx=(0, 14))
        tk.Label(sb, textvariable=self.dur_var, bg="#e8edf3", fg=INK,
                 font=FONT_MONO).grid(row=0, column=11, padx=(0, 14))
        tk.Label(sb, textvariable=self.seg_var, bg="#e8edf3", fg=INK,
                 font=FONT_MONO).grid(row=0, column=12, padx=(0, 14))
        tk.Label(sb, textvariable=self.api_var, bg="#e8edf3", fg=INK,
                 font=FONT_MONO).grid(row=0, column=13, padx=(0, 8))
        tk.Label(sb, text="livello", bg="#e8edf3", fg=MUTED,
                 font=("Segoe UI", 8)).grid(row=0, column=14, padx=(6, 2))
        self.level_bar = ttk.Progressbar(
            sb, variable=self.level_var, maximum=100,
            length=int(150 * self._scale),
            style="LevelOk.Horizontal.TProgressbar")
        self.level_bar.grid(row=0, column=15, padx=(0, 12), pady=8)
        self._level_style = "LevelOk.Horizontal.TProgressbar"
        self.fault_btn = ttk.Button(sb, text="⚠  Riprova trascrizione",
                                    style="Accent.TButton",
                                    command=self._reopen_fault)
        self.fault_btn.grid(row=0, column=16, padx=(0, 12))
        self.fault_btn.grid_remove()
        tk.Label(sb, textvariable=self.hw_var, bg="#e8edf3", fg="#64748b",
                 font=FONT_MONO).grid(row=0, column=17, padx=(0, 12))
        self.dev_lbl = tk.Label(sb, textvariable=self.dev_var, bg="#e8edf3",
                                fg=MUTED, font=FONT_SMALL)
        self.dev_lbl.grid(row=0, column=18, padx=(0, 12))
        self.hw = HwMon()
        self.hw.start()
        self._hw_after = self.after(1000, self._hw_tick)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0,
                                  minsize=int(305 * self._scale))
        self._refresh_context_panel()

    def _refresh_context_panel(self) -> None:
        """Mostra nella sidebar il contesto realmente salvato su disco."""
        notes = str(config.SETTINGS.get("context_notes", "")).strip()
        folder = str(config.SETTINGS.get("context_folder", "")).strip()
        self.ctx_text.configure(state="normal")
        self.ctx_text.delete("1.0", "end")
        if notes:
            self.ctx_text.insert("end", notes)
        else:
            self.ctx_text.insert(
                "end",
                "Nessun contesto salvato.\n\n"
                "Riassunto e Minute usano in questo momento solo la "
                "trascrizione. Premi «Modifica…» per aggiungere note "
                "(tema della call, partecipanti, riferimenti) o una "
                "cartella di progetto.", ("ph",))
        self.ctx_text.configure(state="disabled")
        self.ctx_text.see("1.0")
        if folder:
            p = Path(folder)
            self.ctx_folder_lbl.configure(
                text=f"cartella progetto: {p.name}\n{folder}", fg=INK)
        else:
            self.ctx_folder_lbl.configure(
                text="cartella di progetto: nessuna", fg=MUTED)

    # ------------------------------------------------------------------ #
    # Utilità UI
    # ------------------------------------------------------------------ #
    def _set_pill(self, text: str, bg: str) -> None:
        if self._pill_cache == (text, bg):
            return
        self._pill_cache = (text, bg)
        self.pill.configure(text=f" {text} ", bg=bg)

    def _set_var(self, var: tk.Variable, value: str) -> None:
        """set solo al cambio: evita chiamate Tcl inutili nel poll a 10 Hz."""
        key = str(var)
        if self._var_cache.get(key) != value:
            self._var_cache[key] = value
            var.set(value)

    # Volume basso: finestre di 8 s; se c'è audio ma il picco RMS resta sotto
    # ~-30 dBFS per 2 finestre di fila il parlato rischia di non essere
    # riconosciuto (o di essere trascritto male) -> avviso una volta.
    _VOL_WIN_S = 8.0
    _VOL_SIGNAL = 0.006
    _VOL_LOW = 0.03

    def _reset_volume_check(self) -> None:
        self._vol_win_start = time.monotonic()
        self._vol_win_peak = 0.0
        self._vol_low_runs = 0
        self._vol_warned = False

    def _check_volume(self, level: float) -> None:
        if self.state_ != "running" or self._vol_warned:
            return
        if level > self._vol_win_peak:
            self._vol_win_peak = level
        now = time.monotonic()
        if now - self._vol_win_start < self._VOL_WIN_S:
            return
        peak = self._vol_win_peak
        self._vol_win_start = now
        self._vol_win_peak = 0.0
        if self._VOL_SIGNAL < peak < self._VOL_LOW:
            self._vol_low_runs += 1
        else:
            self._vol_low_runs = 0
        if self._vol_low_runs >= 2:
            self._vol_warned = True
            pct = min(100.0, peak * 350.0)
            msg = (f"\U0001f509 volume basso ({pct:.0f}%): alza il volume "
                   "della chiamata o di Windows, altrimenti la trascrizione "
                   "può perdere parti del parlato")
            self.logger.warning("volume basso: picco=%.4f su %s", peak,
                                self._sess_devices)
            self._sys_line(msg, tag="err")
            self._set_status(msg)
            try:
                self.bell()
            except tk.TclError:
                pass
            # popup dopo il ritorno da _handle: la cattura continua in
            # sottofondo (thread) e il loop Tk resta attivo nel dialog
            self.after_idle(lambda: messagebox.showwarning(
                "Volume basso",
                f"L'audio registrato è molto basso ({pct:.0f}%).\n\n"
                "Alza il volume della chiamata (o di Windows) finché la "
                "barra del livello si muove bene: con volume troppo basso "
                "il parlato non viene riconosciuto e la trascrizione resta "
                "vuota.\n\nLa registrazione continua.", parent=self))

    def _show_level(self, value: float) -> None:
        self.level_var.set(value)
        style = ("LevelHot" if value >= 90 else
                 "LevelWarn" if value >= 70 else
                 "LevelOk") + ".Horizontal.TProgressbar"
        if style != self._level_style:
            self._level_style = style
            self.level_bar.configure(style=style)

    def _set_title(self, title: str) -> None:
        if self._var_cache.get("__title__") != title:
            self._var_cache["__title__"] = title
            self.title(title)

    # ------------------------------------------------------------------ #
    # Arresto automatico (timer)
    # ------------------------------------------------------------------ #
    def _autostop_mode(self) -> str:
        return autostop.LABEL_MODES.get(self.autostop_mode_var.get(), "off")

    def _autostop_value(self, mode: str | None = None) -> str:
        mode = mode or self._autostop_mode()
        if mode == "at":
            return self.autostop_at_var.get().strip()
        return self.autostop_min_var.get().strip()

    def _sync_autostop_widgets(self) -> None:
        mode = self._autostop_mode()
        for w in (self.autostop_min_sb, self.autostop_min_lbl,
                  self.autostop_at_entry):
            w.pack_forget()
        if mode == "after":
            self.autostop_min_sb.pack(side="left")
            self.autostop_min_lbl.pack(side="left", padx=(4, 0))
        elif mode == "at":
            self.autostop_at_entry.pack(side="left")
        row = getattr(self, "autosave_row", None)
        if row is not None:
            if mode == "off" and not self.callend_var.get():
                row.grid_remove()
            else:
                row.grid()
        self._refresh_autostop_desc()

    def _refresh_autostop_desc(self) -> None:
        mode = self._autostop_mode()
        value = self._autostop_value(mode)
        desc = autostop.describe(mode, value)
        valid = mode == "off" or autostop.compute_deadline(
            mode, value, start_mono=0.0, now_mono=0.0) is not None
        if mode == "off":
            text, fg = desc, MUTED
        elif valid:
            text, fg = f"→ {desc}", BLUE
        else:
            text, fg = f"⚠ {desc}", RED
        try:
            self.autostop_desc_var.set(text)
            self.autostop_desc_lbl.configure(fg=fg)
        except tk.TclError:
            pass

    def _persist_autostop(self) -> None:
        """Memorizza solo i valori validi (la modalità vale per la sessione)."""
        auto = dict(config.SETTINGS.get("auto_stop") or {})
        minutes = autostop.parse_minutes(self.autostop_min_var.get())
        hm = autostop.parse_clock(self.autostop_at_var.get())
        changed = False
        if minutes is not None and auto.get("minutes") != minutes:
            auto["minutes"] = minutes
            changed = True
        if hm is not None:
            at = f"{hm[0]:02d}:{hm[1]:02d}"
            if auto.get("at") != at:
                auto["at"] = at
                changed = True
        for key, var in (("save_transcript", self.autosave_tx_var),
                         ("save_summary", self.autosave_sum_var),
                         ("save_minutes", self.autosave_min_var)):
            val = bool(var.get())
            if auto.get(key) != val:
                auto[key] = val
                changed = True
        if changed:
            config.SETTINGS["auto_stop"] = auto
            config.save_settings()

    def _on_autostop_change(self) -> None:
        self._sync_autostop_widgets()
        self._persist_autostop()
        if self.state_ == "running":
            self._arm_autostop(changed=True)

    def _arm_autostop(self, changed: bool = False) -> None:
        """Calcola la scadenza; a sessione in corso applica le modifiche."""
        mode = self._autostop_mode()
        now = time.monotonic()
        old = self._autostop_deadline
        dl = autostop.compute_deadline(
            mode, self._autostop_value(mode),
            start_mono=self.started_mono or now, now_mono=now)
        if dl is not None and changed and dl <= now + 1:
            dl = now + autostop.GRACE_S
            self._sys_line("il nuovo limite del timer è già superato: "
                           "arresto automatico tra 1 minuto")
        if (dl is None and old is None) or (
                dl is not None and old is not None and abs(dl - old) < 1):
            return
        self._autostop_deadline = dl
        self._autostop_warned = (dl is not None
                                 and dl - now <= autostop.WARN_BEFORE_S)
        if dl is None:
            self._set_var(self.timer_var, "")
            if changed and old is not None:
                self._sys_line("arresto automatico disattivato")
                self.logger.info("timer arresto disattivato")
            return
        desc = autostop.describe(mode, self._autostop_value(mode))
        self._sys_line(f"\u23f1 arresto automatico: {desc} "
                       f"(tra {autostop.format_remaining(dl - now)})")
        self.logger.info("timer arresto: %s (tra %.0fs)", desc, dl - now)

    def _tick_autostop(self, now: float) -> None:
        dl = self._autostop_deadline
        if dl is None:
            return
        remaining = dl - now
        if remaining <= 0:
            self._autostop_deadline = None
            self._autostop_fired = True
            self._autostop_reason = "timer"
            self._set_var(self.timer_var, "")
            what = "audio + video" if self._screen_rec is not None \
                else "audio"
            self.logger.info("timer scaduto: arresto automatico (%s)", what)
            self._sys_line(f"\u23f1 timer scaduto: arresto automatico "
                           f"della registrazione ({what})")
            self._on_stop()
            return
        if not self._autostop_warned and remaining <= autostop.WARN_BEFORE_S:
            self._autostop_warned = True
            self._sys_line("\u23f1 la registrazione si fermerà "
                           "automaticamente tra 1 minuto")
            try:
                self.bell()
            except tk.TclError:
                pass
        self._set_var(self.timer_var,
                      f"\u23f1 stop tra {autostop.format_remaining(remaining)}")

    # ------------------------------------------------------------------ #
    # Arresto a fine chiamata
    # ------------------------------------------------------------------ #
    def _on_callend_toggle(self) -> None:
        self._sync_autostop_widgets()
        if self.state_ == "running":
            self._arm_callwatch()

    def _arm_callwatch(self) -> None:
        if not self.callend_var.get():
            if self._callwatch is not None:
                self._stop_callwatch()
                self._sys_line("arresto a fine chiamata disattivato")
                self.logger.info("fine chiamata: disattivato")
            return
        if self._callwatch is not None:
            return
        self._callwatch = callwatch.CallWatch()
        self._micprobe = callwatch.MicProbe()
        self._callwatch_next = 0.0
        self._callwatch_warned = False
        self._sys_line("\U0001f4de arresto a fine chiamata attivo: mi "
                       "fermo quando l'app di chiamata chiude il microfono "
                       "(senza app riconosciuta: dopo 5 min di silenzio)")
        self.logger.info("fine chiamata: attivo")

    def _stop_callwatch(self) -> None:
        self._callwatch = None
        probe = getattr(self, "_micprobe", None)
        self._micprobe = None
        if probe is not None:
            probe.stop()

    def _tick_callwatch(self, now: float) -> None:
        cw = self._callwatch
        probe = getattr(self, "_micprobe", None)
        if cw is None or probe is None or now < self._callwatch_next:
            return
        self._callwatch_next = now + callwatch.POLL_S
        apps = probe.latest
        if probe.checked and apps is None and not self._callwatch_warned:
            self._callwatch_warned = True
            self._sys_line("\U0001f4de fine chiamata: uso del microfono non "
                           "leggibile, mi fermo dopo 5 min di silenzio",
                           tag="err")
            self.logger.warning("fine chiamata: sessioni audio illeggibili")
        ev = cw.poll(apps if probe.checked else None, now)
        names = ", ".join(sorted(cw.apps)) or "chiamata"
        if ev == "call":
            self._sys_line(f"\U0001f4de chiamata rilevata ({names}): mi "
                           "fermo quando finisce")
            self.logger.info("fine chiamata: rilevata %s", names)
        elif ev == "gone":
            self._sys_line(f"\U0001f4de {names} ha rilasciato il microfono: "
                           f"stop tra {callwatch.CONFIRM_S:.0f} s se la "
                           "chiamata non riprende")
            self.logger.info("fine chiamata: microfono rilasciato")
        elif ev == "back":
            self._sys_line("\U0001f4de chiamata ripresa: continuo")
            self.logger.info("fine chiamata: ripresa")
        elif ev in ("end", "silence", "idle"):
            why = ("chiamata terminata" if ev == "end" else
                   f"{callwatch.SILENCE_S / 60:.0f} min di silenzio")
            self._stop_callwatch()
            self._autostop_fired = True
            self._autostop_reason = "fine chiamata"
            what = "audio + video" if self._screen_rec is not None \
                else "audio"
            self.logger.info("fine chiamata: %s, arresto (%s)", why, what)
            self._sys_line(f"\U0001f4de {why}: arresto automatico della "
                           f"registrazione ({what})")
            self._on_stop()
            return
        rem = cw.remaining(now)
        if rem is not None and not self._autostop_deadline:
            self._set_var(self.timer_var,
                          f"\U0001f4de stop tra "
                          f"{autostop.format_remaining(rem)}")
        elif ev == "back" and not self._autostop_deadline:
            self._set_var(self.timer_var, "")

    def _browse_out(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self, title="Scegli la cartella di destinazione")
        if chosen:
            self.out_dir_var.set(chosen)
            config.SETTINGS["output_dir"] = chosen
            config.save_settings()
            self.logger.info("output_dir aggiornata: %s", chosen)

    def _browse_temp(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self, title="Scegli la cartella temporanea")
        if chosen:
            self.temp_dir_var.set(chosen)
            config.SETTINGS["temp_root"] = chosen
            config.save_settings()
            self.logger.info("temp_root aggiornato: %s", chosen)
            self._sys_line(f"cartella temporanea: {chosen}")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, on_saved=self._on_settings_saved)
        self.wait_window(dlg)
        if dlg.result:
            self._sys_line("configurazione aggiornata e salvata")

    def _on_settings_saved(self) -> None:
        s = config.SETTINGS
        self.out_dir_var.set(str(s.get("output_dir", self.out_dir_var.get())))
        self.temp_dir_var.set(str(config.get_temp_root()))
        self.bilingual_var.set(bool(s.get("bilingual", True)))
        self._update_provider_label()
        if self._fault_dlg is not None and self._fault_dlg.winfo_exists():
            stt = s.get("stt", {})
            cur = (stt.get("model") or "").strip()
            self._fault_dlg.set_models(self._suggest_models(stt, cur), cur)
            self._fault_dlg.model_var.set(cur)
        self.logger.info(
            "settings salvati: stt=%s/%s text=%s/%s",
            s["stt"].get("type"), s["stt"].get("model"),
            s["text"].get("type"), s["text"].get("model"))

    @staticmethod
    def _provider_desc(cfg: dict) -> str:
        model = (cfg.get("model") or "").strip() or "?"
        typ = cfg.get("type", "gemini")
        if typ == "gemini":
            return f"gemini:{model}"
        if typ == "offline":
            name = (model if model in config.OFFLINE_SIZES
                    else config.DEFAULT_OFFLINE_MODEL)
            return f"offline:whisper-{name}"
        base = (cfg.get("base_url") or "").strip() or "?"
        return f"endpoint:{model}@{base}"

    def _update_provider_label(self) -> None:
        missing = config.missing_credentials()
        stt = config.SETTINGS["stt"]
        txt = config.SETTINGS["text"]
        if missing:
            self.provider_label.configure(
                text="configurazione incompleta \u2699", fg=RED)
        else:
            self.provider_label.configure(
                text=f"stt {self._provider_desc(stt)} · "
                     f"testo {self._provider_desc(txt)}", fg=GREEN)
        self.header_model.configure(
            text=f"modello: {self._provider_desc(stt)}")

    def _load_devices(self) -> None:
        try:
            devs = list_audio_devices()
        except Exception as exc:
            self._loopbacks, self._mics = [], []
            self.lb_combo.configure(values=[])
            self.mic_combo.configure(values=["Disattivato"])
            self._set_status(f"dispositivi audio non disponibili: {exc}")
            return
        self._loopbacks = devs["loopbacks"]
        self._mics = devs["mics"]
        self.lb_combo.configure(
            values=[d["name"] for d in self._loopbacks] or ["nessuno"])
        if devs["default_loopback"]:
            self.lb_var.set(devs["default_loopback"]["name"])
        elif self._loopbacks:
            self.lb_var.set(self._loopbacks[0]["name"])
        self.mic_combo.configure(
            values=["Disattivato"] + [d["name"] for d in self._mics])
        self.mic_var.set("Disattivato")

    def _selected_loopback(self) -> dict | None:
        name = self.lb_var.get()
        for d in self._loopbacks:
            if d["name"] == name:
                return d
        return self._loopbacks[0] if self._loopbacks else None

    def _selected_mic(self) -> dict | None:
        name = self.mic_var.get()
        if name == "Disattivato":
            return None
        for d in self._mics:
            if d["name"] == name:
                return d
        return None

    def _update_controls(self) -> None:
        st = self.state_
        cfg_ok = st in ("idle", "stopped") and not self.generating
        state_cfg = "normal" if cfg_ok else "disabled"
        for w in (self.browse_btn, self.lb_combo, self.mic_combo,
                  self.refresh_btn, self.config_btn, self.temp_browse_btn,
                  self.temp_entry, self.out_entry, self.video_cb):
            w.configure(state=state_cfg)
        self.start_btn.configure(
            state="normal" if cfg_ok else "disabled")
        self.stop_btn.configure(state="normal" if st == "running" else "disabled")
        has = self._has_content()
        gen_ok = st == "stopped" and has and not self.generating
        self.tx_btn.configure(state="normal" if gen_ok else "disabled")
        self.summary_btn.configure(state="normal" if gen_ok else "disabled")
        self.minutes_btn.configure(state="normal" if gen_ok else "disabled")
        rec_ok = (st == "stopped" and self._has_recording()
                  and not self.generating)
        self.rec_btn.configure(state="normal" if rec_ok else "disabled")
        vid_ok = (st == "stopped" and self._has_video()
                  and not self.generating)
        self.video_btn.configure(state="normal" if vid_ok else "disabled")
        self.analysis_btn.configure(state="normal" if vid_ok else "disabled")
        # l'analisi da file non dipende dalla sessione corrente: basta che
        # non ci sia gia' una generazione attiva
        self.deferred_btn.configure(
            state="disabled" if self.generating else "normal")
        self.context_btn.configure(
            state="disabled" if self.generating else "normal")

        if self.generating:
            self._set_pill("GENERO.", AMBER)
        elif st == "running" and self.fault_info is not None:
            self._set_pill("⚠ IN PAUSA", RED)
        elif st == "running":
            self._set_pill("● IN ASCOLTO", RED)
        elif st == "stopping":
            self._set_pill("FERMATURA…", AMBER)
        elif st == "stopped":
            self._set_pill("CONCLUSA", GREEN)
        else:
            self._set_pill("PRONTO", "#475569")

    def _set_status(self, msg: str) -> None:
        # solo al cambio: il messaggio viene ricalcolato ad ogni tick di
        # _poll (10 Hz) e StringVar.set = chiamata Tcl inutile se identico
        if msg != self._status_cache:
            self._status_cache = msg
            self.status_var.set(msg)

    def _has_content(self) -> bool:
        return any((r.get("italian") or "").strip()
                   or (r.get("original") or "").strip()
                   for r in self.results)

    def _has_recording(self) -> bool:
        try:
            return (self._wav_temp is not None
                    and Path(self._wav_temp).is_file()
                    and Path(self._wav_temp).stat().st_size > 0)
        except OSError:
            return False

    def _default_out_dir(self) -> str:
        d = self.out_dir_var.get().strip()
        return d if d else str(Path.home() / "Documents")

    def _on_save_tx(self) -> None:
        """Salva la trascrizione: cartella e nome scelti dall'utente."""
        if not self._has_content() or self.state_ != "stopped":
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Salva trascrizione",
            defaultextension=".md", initialdir=self._default_out_dir(),
            initialfile=f"trascrizione_{self.stamp}.md",
            filetypes=[("Markdown", "*.md"), ("Tutti i file", "*.*")])
        if not path:
            return
        try:
            tx = outputs.build_transcript_md(self.results,
                                             self.final_duration)
            saved = outputs.write_text(Path(path), tx)
        except Exception as exc:
            self.logger.exception("salvataggio trascrizione fallito")
            messagebox.showerror("Salvataggio non riuscito", str(exc),
                                 parent=self)
            return
        self.logger.info("trascrizione salvata: %s", saved)
        self._session_saved = True
        self._sys_line(f"trascrizione salvata: {saved}")
        messagebox.showinfo("Salvato", f"File:\n\n{saved}", parent=self)

    def _has_video(self) -> bool:
        try:
            return (self._video_temp is not None
                    and Path(self._video_temp).is_file()
                    and Path(self._video_temp).stat().st_size > 0)
        except OSError:
            return False

    def _start_save(self, title: str, message: str, work) -> None:
        """Presta l'operazione lunga in un thread con finestra di progresso.

        ``work``: callable senza argomenti che ritorna un dict
        {"kind": "info"/"warning"/"error", "title", "msg"}; comunica
        l'avanzamento con _save_progress (coda eventi UI).
        """
        dlg = SaveProgressDialog(self, title, message)
        self._save_dlg = dlg
        cancel = threading.Event()
        dlg.on_cancel = cancel.set
        self._save_cancel = cancel

        def worker() -> None:
            try:
                res = work()
            except Exception as exc:            # noqa: BLE001
                self.logger.exception("salvataggio in background fallito")
                res = {"kind": "error", "title": title,
                       "msg": str(exc)}
            self.q.put(("savedone", res))

        threading.Thread(target=worker, daemon=True,
                         name=f"save-{title}").start()

    def _save_progress(self, frac: float, msg: str = "") -> None:
        self.q.put(("saveprog", {"p": float(frac), "msg": msg}))

    def _on_save_rec(self) -> None:
        """Salva la registrazione (WAV o MP3) con barra di avanzamento."""
        if self.state_ != "stopped" or not self._has_recording():
            messagebox.showinfo(
                "Registrazione non disponibile",
                "Nessuna registrazione: avvia una sessione prima.",
                parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Salva registrazione",
            defaultextension=".wav", initialdir=self._default_out_dir(),
            initialfile=f"registrazione_{self.stamp}.wav",
            filetypes=[("Audio WAV", "*.wav"), ("Audio MP3", "*.mp3"),
                       ("Tutti i file", "*.*")])
        if not path:
            return
        self._start_save("Salvataggio registrazione",
                         "preparazione del file audio…",
                         self._rec_work(Path(path)))

    def _rec_work(self, dest: Path):
        """Lavoro (thread) che salva l'audio della sessione in ``dest``."""
        def work():
            if dest.suffix.lower() == ".mp3":
                self._save_progress(0.0, "conversione WAV → MP3 in corso…")
            else:
                self._save_progress(0.0, "copia dell'audio in corso…")
            res = recorder.save_recording(
                Path(self._wav_temp), dest,
                on_progress=lambda f: self._save_progress(f),
                cancel_event=self._save_cancel)
            if res["cancelled"]:
                return {"kind": "info", "title": "Salvataggio annullato",
                        "msg": "Il salvataggio è stato annullato; nessun "
                               "file è stato lasciato sul disco."}
            if res["path"] is not None:
                self.logger.info("registrazione salvata: %s", res["path"])
                self.q.put(("sys", f"registrazione salvata: {res['path']}"))
                return {"kind": "info", "title": "Salvato",
                        "msg": f"File:\n\n{res['path']}"}
            return {"kind": "error", "title": "Salvataggio non riuscito",
                    "msg": "\n".join(res["warnings"]) or "errore sconosciuto"}
        return work

    def _on_save_video(self) -> None:
        """Salva il video (mux audio) con barra di avanzamento e Annulla."""
        if self.state_ != "stopped" or not self._has_video():
            messagebox.showinfo(
                "Video non disponibile",
                "Nessun video da salvare: avvia una sessione "
                "con la casella «Con video» attiva.",
                parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Salva video", defaultextension=".mp4",
            initialdir=self._default_out_dir(),
            initialfile=f"schermo_{self.stamp}.mp4",
            filetypes=[("Video MP4", "*.mp4"), ("Tutti i file", "*.*")])
        if not path:
            return
        self._start_save("Salvataggio video",
                         "preparazione del file video…",
                         self._video_work(Path(path)))

    def _video_work(self, dest: Path):
        """Lavoro (thread) che salva il video con l'audio in ``dest``."""
        def work():
            self._save_progress(0.0, "unione della traccia audio al video "
                                      "(codifica mp4, puo' durare minuti)…")
            wav = Path(self._wav_temp) if self._wav_temp else None
            merged = None
            if wav is not None and wav.is_file():
                merged = screenrec.mux_audio(
                    Path(self._video_temp), wav, dest,
                    on_progress=self._save_progress,
                    cancel_event=self._save_cancel)
            if self._save_cancel.is_set():
                return {"kind": "info", "title": "Salvataggio annullato",
                        "msg": "Il salvataggio è stato annullato; nessun "
                               "file è stato lasciato sul disco."}
            if merged is None:
                self._save_progress(0.0, "mux non riuscito: copia del video "
                                         "senza audio…")
                try:
                    total = Path(self._video_temp).stat().st_size or 1
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(self._video_temp, "rb") as fsrc, \
                            open(dest, "wb") as fdst:
                        copied = 0
                        while True:
                            chunk = fsrc.read(4 << 20)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            copied += len(chunk)
                            self._save_progress(copied / total)
                    if self._save_cancel.is_set():
                        try:
                            dest.unlink()
                        except Exception:   # noqa: BLE001
                            pass
                        return {"kind": "info",
                                "title": "Salvataggio annullato",
                                "msg": "Il salvataggio è stato annullato."}
                except Exception as exc:        # noqa: BLE001
                    return {"kind": "error",
                            "title": "Salvataggio non riuscito",
                            "msg": str(exc)}
                self.q.put(("sys", f"video salvato SENZA audio: {dest}"))
                return {"kind": "warning", "title": "Salvato senza audio",
                        "msg": ("Il mux audio non è riuscito, video "
                                "salvato senza traccia audio:\n\n" +
                                str(dest))}
            self.logger.info("video salvato: %s", merged)
            self.q.put(("sys", f"video salvato (con audio): {merged}"))
            return {"kind": "info", "title": "Salvato",
                    "msg": f"File (con audio incorporato):\n\n{merged}"}
        return work

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """Non sovrascrive mai: aggiunge _1, _2… se il file esiste."""
        if not path.exists():
            return path
        for i in range(1, 1000):
            cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
            if not cand.exists():
                return cand
        return path

    def _autosave_after_timer(self) -> bool:
        """Timer scaduto: salva da solo nella cartella destinazione.

        Sessione con video -> MP4 con audio incorporato; senza video (o se
        il video è fallito) -> audio MP3 (WAV se manca ffmpeg). Poi, secondo
        le caselle «allo scadere salva anche», trascrizione (.md), riassunto
        (.md) e minute (.md + .docx) generati dall'AI. Ritorna False se non
        c'è nulla da salvare.
        """
        out_dir = Path(self._default_out_dir())
        if self._has_video():
            dest = self._unique_path(out_dir / f"schermo_{self.stamp}.mp4")
            media_work = self._video_work(dest)
            title = "Salvataggio automatico video"
        elif self._has_recording():
            ext = ".mp3" if recorder.ffmpeg_path() else ".wav"
            dest = self._unique_path(
                out_dir / f"registrazione_{self.stamp}{ext}")
            media_work = self._rec_work(dest)
            title = "Salvataggio automatico registrazione"
        else:
            self._sys_line("timer: nessuna registrazione da salvare",
                           tag="err")
            return False
        results = list(self.results)
        duration = self.final_duration
        stamp = self.stamp
        has_tx = self._has_content()
        want_tx = has_tx and self.autosave_tx_var.get()
        docs = [k for k, var in (("summary", self.autosave_sum_var),
                                 ("minutes", self.autosave_min_var))
                if var.get()]
        doc_skip = ""
        if docs and not has_tx:
            doc_skip = "nessuna trascrizione: riassunto/minute non generati"
            docs = []
        elif docs:
            missing = config.missing_credentials()
            if missing:
                doc_skip = ("riassunto/minute non generati — "
                            + "; ".join(missing))
                docs = []
        if doc_skip:
            self._sys_line(f"timer: {doc_skip}", tag="err")
        settings = config.SETTINGS

        def cancelled() -> bool:
            return (self._save_cancel is not None
                    and self._save_cancel.is_set())

        def work():
            res = media_work()
            extra: list[str] = []
            problems: list[str] = [doc_skip] if doc_skip else []
            if want_tx and not cancelled():
                try:
                    saved = outputs.write_text(
                        self._unique_path(
                            out_dir / f"trascrizione_{stamp}.md"),
                        outputs.build_transcript_md(results, duration))
                    self.q.put(("sys", f"trascrizione salvata: {saved}"))
                    extra.append(f"Trascrizione:\n{saved}")
                except Exception as exc:        # noqa: BLE001
                    self.logger.warning("trascrizione automatica: %s", exc)
                    problems.append(f"trascrizione: {exc}")
            context_text = None
            for i, kind in enumerate(docs):
                if cancelled():
                    break
                label = "Minute" if kind == "minutes" else "Riassunto"
                self._save_progress(
                    i / len(docs),
                    f"generazione {label.lower()} con l'AI "
                    f"({i + 1}/{len(docs)})…")
                try:
                    if context_text is None:
                        context_text = self._build_context()
                    system, user = self._doc_prompts(
                        kind, results, duration, context_text,
                        "(nessuna analisi video per questa sessione)")
                    self.logger.info("timer: generazione %s", kind)
                    text = service.complete(settings, system, user,
                                            on_status=self._q_status)
                    if len(text.strip()) < 20:
                        raise RuntimeError("il modello ha restituito un "
                                           "testo vuoto")
                    stem = DOC_STEM.get(kind, "riassunto")
                    md = outputs.write_text(
                        self._unique_path(out_dir / f"{stem}_{stamp}.md"),
                        text)
                    paths = [md]
                    if kind == "minutes":
                        paths.append(outputs.markdown_to_docx(
                            text, md.with_suffix(".docx")))
                    for p in paths:
                        self.q.put(("sys", f"{label.lower()} salvato: {p}"))
                    extra.append(f"{label}:\n"
                                 + "\n".join(str(p) for p in paths))
                except Exception as exc:        # noqa: BLE001
                    self.logger.warning("timer: %s non generato: %s",
                                        kind, exc)
                    problems.append(f"{label.lower()}: {exc}")
            if extra:
                res["msg"] = ((res.get("msg") or "") + "\n\n"
                              + "\n\n".join(extra))
            if problems:
                for p in problems:
                    self.q.put(("sys", f"timer: {p}"))
                res["msg"] = ((res.get("msg") or "") + "\n\n⚠ "
                              + "\n⚠ ".join(problems))
                if res.get("kind") == "info":
                    res["kind"] = "warning"
            res["title"] = ({"timer": "\u23f1 Timer scaduto — ",
                             "fine chiamata": "\U0001f4de Chiamata "
                                              "terminata — "}.get(
                                self._autostop_reason, "\U0001f4be ")
                            + str(res.get("title") or "Salvato"))
            return res

        self.logger.info("timer: salvataggio automatico in %s", dest)
        self._sys_line(f"{self._autostop_reason}: salvataggio automatico in "
                       f"{out_dir}")
        self._start_save(title, f"salvataggio automatico in {dest.name}…",
                         work)
        return True

    # ------------------------------------------------------------------ #
    # Callback dai thread (-> coda eventi)
    # ------------------------------------------------------------------ #
    def _on_segment(self, start_s: float, pcm) -> None:
        self._seg_count += 1
        if self.pipeline is not None:
            self.pipeline.submit(start_s, pcm)

    def _q_level(self, v: float) -> None:
        self.q.put(("level", v))

    def _q_status(self, m: str) -> None:
        self.q.put(("status", m))

    def _q_error(self, m: str) -> None:
        self.q.put(("error", m))

    def _q_fault(self, info: dict) -> None:
        self.q.put(("fault", info))

    def _on_result(self, r: dict) -> None:
        # eseguito nel thread della pipeline
        self.results.append(r)
        self.q.put(("segment", r))
        if ((r.get("italian") or "").strip()
                or (r.get("original") or "").strip() or r.get("error")):
            self._tx_dirty.set()        # il writer scrive in batch

    def _tx_write_worker(self) -> None:
        """Scrive transcript_live.md al massimo ogni ~2 s (ultimo stato)."""
        while True:
            stopping = self._tx_write_stop.is_set()
            if self._tx_dirty.is_set():
                self._tx_dirty.clear()
                self._tx_flush()
            if stopping:
                return
            if self._tx_dirty.wait(timeout=0.5):
                # accumula i segmenti in arrivo; si sveglia subito allo stop
                self._tx_write_stop.wait(2.0)

    def _tx_flush(self) -> None:
        pending = list(self.results)
        if not pending:
            return
        elapsed = (time.monotonic() - self.started_mono
                   if self.started_mono else self.final_duration)
        try:
            outputs.write_text(
                self.session_dir / "transcript_live.md",
                outputs.build_transcript_md(pending, elapsed))
        except Exception:
            # warning (non debug): altrimenti la trascrizione live smette
            # di aggiornarsi senza che nessuno se ne accorga
            self.logger.warning("scrittura di transcript_live.md fallita",
                                exc_info=True)

    # ------------------------------------------------------------------ #
    # Errore di trascrizione -> dialog di ripresa (registrazione attiva)
    # ------------------------------------------------------------------ #
    def _on_fault(self, info: dict) -> None:
        self.fault_info = info
        self.logger.error("trascrizione in pausa: %s", info.get("error"))
        self._set_status("trascrizione in pausa per errore — "
                         "la registrazione continua")
        self._sys_line(f"errore trascrizione: {info.get('error')}", tag="err")
        self._sys_line("segmenti restati in coda: la registrazione audio "
                       "prosegue, scegli come riprendere", tag="err")
        self.fault_btn.grid()
        self._update_controls()
        self._open_fault_dialog()

    def _open_fault_dialog(self) -> None:
        info = self.fault_info or {}
        if self._fault_dlg is not None and self._fault_dlg.winfo_exists():
            self._fault_dlg.update_info(info)
            self._fault_dlg.reopen()
            return
        stt = config.SETTINGS.get("stt", {})
        current = (stt.get("model") or "").strip()
        self._fault_dlg = FaultDialog(self, info,
                                      self._suggest_models(stt, current),
                                      current, self._fault_action)
        self._fault_dlg.reopen()
        if stt.get("type") != "offline":
            threading.Thread(target=self._load_models_async, daemon=True,
                             name="models-list").start()

    def _reopen_fault(self) -> None:
        if self.fault_info is None:
            return
        if self._fault_dlg is None or not self._fault_dlg.winfo_exists():
            self._fault_dlg = None
            self._open_fault_dialog()
        else:
            self._fault_dlg.reopen()

    @staticmethod
    def _suggest_models(stt: dict, current: str) -> list[str]:
        """Elenco immediato (senza rete): il completo arriva in async."""
        if stt.get("type") == "offline":
            models = list(config.OFFLINE_SIZES)
        else:
            models = list(config.SUGGESTED_MODELS)
        if current and current not in models:
            models = [current] + models
        return models

    def _load_models_async(self) -> None:
        stt = config.SETTINGS.get("stt", {})
        current = (stt.get("model") or "").strip()
        try:
            models = service.available_models(stt)
        except Exception as exc:                        # noqa: BLE001
            self.logger.info("elenco modelli non caricato: %s", exc)
            return
        self.q.put(("models", {"models": models, "current": current}))

    def _fault_action(self, action: str, model: str) -> None:
        """Scelta dell'utente nel dialog di errore."""
        pipe = self.pipeline
        if pipe is None:
            return
        if action == "config":
            self._open_settings()
            # il dialog di errore resta disponibile per la ripresa
            if self._fault_dlg is not None and self._fault_dlg.winfo_exists():
                self._fault_dlg.reopen()
            return
        stt = config.SETTINGS.get("stt", {})
        if model and model != (stt.get("model") or "").strip():
            stt["model"] = model
            config.save_settings()
            self._update_provider_label()
            self._sys_line(f"modello di trascrizione cambiato in «{model}»")
            self.logger.info("modello stt cambiato in %s", model)
        if action == "skip":
            n = pipe.resume("skip")
            self._sys_line(f"{n} segmenti saltati per errore")
        else:
            n = pipe.resume("retry")
            self._sys_line(f"trascrizione ripresa: {n} segmenti in ripresa")
        self.fault_info = None
        self.fault_btn.grid_remove()
        self._set_status("in ascolto…")
        self._update_controls()

    # ------------------------------------------------------------------ #
    # Avvio / fermata
    # ------------------------------------------------------------------ #
    def _on_start(self) -> None:
        if self.state_ not in ("idle", "stopped"):
            return
        out = self.out_dir_var.get().strip()
        missing = config.missing_credentials()
        if missing:
            messagebox.showwarning(
                "Configurazione incompleta",
                "Mancano i dati per l'IA:\n\n• " + "\n• ".join(missing) +
                "\n\nSi apre la finestra di Configurazione.", parent=self)
            self._open_settings()
            return
        lb = self._selected_loopback()
        if lb is None:
            messagebox.showerror(
                "Audio di sistema non trovato",
                "Nessun dispositivo di loopback (audio di sistema) "
                "disponibile.", parent=self)
            return
        as_mode = self._autostop_mode()
        if as_mode != "off" and autostop.compute_deadline(
                as_mode, self._autostop_value(as_mode),
                start_mono=0.0, now_mono=0.0) is None:
            messagebox.showwarning(
                "Arresto automatico",
                "Valore del timer non valido: "
                f"{autostop.describe(as_mode, self._autostop_value(as_mode))}"
                ".\n\nCorreggilo oppure scegli «Disattivato».", parent=self)
            return

        # persiste le cartelle scelte e prepara la sessione
        temp_root = self.temp_dir_var.get().strip()
        if temp_root:
            config.SETTINGS["temp_root"] = temp_root
        if out:
            config.SETTINGS["output_dir"] = out
        # la scelta «Con video» vale solo per questa sessione: non viene
        # persistita (il video temporaneo riempiva disco senza definizione)
        config.save_settings()
        try:
            recorder.cleanup_session(self.session_dir)
        except Exception:
            pass
        try:
            config.purge_temp_sessions()        # via i temporanei precedenti
        except Exception:
            pass
        self.session_dir = config.new_session_dir()
        self._video_temp = None
        self.video_analysis = None
        self.stamp = time.strftime("%Y%m%d_%H%M%S")
        self.results = []
        self._session_saved = False
        self._lang_mismatch = 0
        self.final_duration = 0.0
        self.fault_info = None
        if self._fault_dlg is not None and self._fault_dlg.winfo_exists():
            self._fault_dlg.destroy()
        self._fault_dlg = None
        self.fault_btn.grid_remove()
        self._clear_text()
        self._wav_temp = recorder.temp_wav_path(self.session_dir)
        self._seg_count = 0
        self._sess_peak = 0.0
        self._reset_volume_check()
        self._sess_devices = lb.get("name", "?")
        mic = self._selected_mic()
        if mic is not None:
            self._sess_devices += f" + {mic.get('name', '?')}"

        self.pipeline = Pipeline(
            config.SETTINGS,
            on_result=self._on_result, on_status=self._q_status,
            on_error=self._q_error, on_fault=self._q_fault)
        self.pipeline.bilingual = self.bilingual_var.get()

        try:
            self.audio = AudioSession(
                lb, mic, self._wav_temp,
                on_segment=self._on_segment, on_level=self._q_level,
                on_error=self._q_error, on_status=self._q_status)
            self.audio.start()
        except Exception as exc:
            self.audio = None
            self.pipeline = None
            messagebox.showerror("Avvio non riuscito", str(exc), parent=self)
            return

        if self.video_var.get():
            try:
                self._start_screen_rec()
            except Exception as exc:            # noqa: BLE001
                self._screen_rec = None
                self.logger.warning("video non disponibile: %s", exc)
                self._sys_line(f"registrazione video non disponibile: "
                               f"{exc}")

        self.pipeline.start()
        self.state_ = "running"
        self.started_mono = time.monotonic()
        self._autostop_deadline = None
        self._autostop_fired = False
        self._autostop_reason = "timer"
        self._stop_callwatch()
        self._update_controls()
        self._set_status("in ascolto…")
        self.logger.info(
            "avvio sessione dir=%s audio=%s bilingual=%s stt=%s text=%s",
            self.session_dir, self._sess_devices, self.bilingual_var.get(),
            self._provider_desc(config.SETTINGS["stt"]),
            self._provider_desc(config.SETTINGS["text"]))
        self._sys_line(f"sessione avviata — temporanea: {self.session_dir}")
        self._sys_line("nulla viene salvato in automatico: al termine usa i "
                       "pulsanti «Salva…» (l'audio temporaneo sparisce alla "
                       "chiusura)")
        mode = ("originale + italiano" if self.bilingual_var.get()
                else "solo italiano")
        self._sys_line(f"modalità: {mode} · stt: "
                       f"{self._provider_desc(config.SETTINGS['stt'])}")
        self._arm_autostop()
        self._arm_callwatch()
        # PC lasciato da solo: niente sospensione; con il video anche niente
        # screensaver / schermo spento (la cattura riprenderebbe quello)
        with_video = self._screen_rec is not None
        if power.keep_awake(display=with_video):
            self._sys_line(
                "sospensione del PC bloccata durante la registrazione"
                + (" (anche screensaver e spegnimento schermo, per il video)"
                   if with_video else ""))

    def _on_stop(self) -> None:
        if self.state_ != "running":
            return
        # fermata a mano con timer / fine chiamata attivi: l'utente ha già
        # scelto il salvataggio automatico, quindi si salva comunque
        if not self._autostop_fired and (
                self._autostop_deadline is not None
                or self._callwatch is not None):
            cw = self._callwatch
            self._autostop_fired = True
            self._autostop_reason = (
                "fine chiamata" if cw is not None
                and cw.gone_since is not None else "fermata manuale")
            self.logger.info("fermata con arresto automatico attivo: "
                             "salvataggio automatico (%s)",
                             self._autostop_reason)
        self.state_ = "stopping"
        self._autostop_deadline = None
        self._stop_callwatch()
        self._set_var(self.timer_var, "")
        self._set_title(self._base_title)
        self._update_controls()
        self._show_stop_overlay()
        self._set_status("chiusura in corso… attendo i segmenti "
                         "in elaborazione")
        self.logger.info(
            "fermata richiesta (durata %.1fs)",
            (time.monotonic() - self.started_mono)
            if self.started_mono else 0.0)
        threading.Thread(target=self._stop_worker, daemon=True,
                         name="stopper").start()

    def _stop_worker(self) -> None:
        info: dict = {"duration": 0.0, "warnings": []}
        # 1) fermo subito TUTTE le catture: se il video (o l'audio)
        #    continuassero mentre la pipeline svuota la coda, il filmato
        #    sarebbe molto piu' lungo dell'audio registrato
        try:
            self._stop_screen_rec(info)
        except Exception as exc:
            info["warnings"].append(f"fermata video: {exc}")
        try:
            if self.audio is not None:
                stats = self.audio.stop()
                info["duration"] = float(stats.get("duration", 0.0))
        except Exception as exc:
            info["warnings"].append(f"fermata audio: {exc}")
        # 2) poi si lascia alla pipeline completare i segmenti gia' in coda
        #    (trascrizione + traduzione): qui si ferma la schermata di attesa
        try:
            if self.pipeline is not None:
                self.pipeline.stop(drain=True, timeout=config.STOP_DRAIN_S)
        except Exception as exc:
            info["warnings"].append(f"fermata pipeline: {exc}")

        self.final_duration = info["duration"]
        self.q.put(("stopped", info))

    # --- schermata «attendi» durante la chiusura ------------------------ #
    def _show_stop_overlay(self) -> None:
        """Schermata con loader: copre la finestra finché la chiusura dura."""
        if getattr(self, "_stop_ovl", None) is not None:
            return
        ovl = tk.Frame(self, bg=BG)
        ovl.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = tk.Frame(ovl, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1, padx=32, pady=24)
        bar = ttk.Progressbar(box, mode="indeterminate", length=240)
        bar.pack(pady=(0, 14))
        bar.start(12)
        tk.Label(box, text="Sessione in chiusura…", bg=CARD, fg=INK,
                 font=FONT_HEADER).pack()
        self._stop_ovl_msg = tk.Label(
            box, text="Attendi: completamento dei segmenti in corso",
            bg=CARD, fg=MUTED, font=FONT_SMALL)
        self._stop_ovl_msg.pack(pady=(8, 0))
        box.place(relx=0.5, rely=0.5, anchor="center")
        ovl.lift()
        self._stop_ovl = ovl
        self._stop_ovl_bar = bar

    def _update_stop_overlay(self, msg: str) -> None:
        lbl = getattr(self, "_stop_ovl_msg", None)
        try:
            if lbl is not None and lbl.winfo_exists():
                if str(lbl.cget("text")) != msg:
                    lbl.configure(text=msg)
        except tk.TclError:
            pass

    def _hide_stop_overlay(self) -> None:
        ovl = getattr(self, "_stop_ovl", None)
        if ovl is None:
            return
        self._stop_ovl = None
        # ferma lo spinner prima della destroy: altrimenti il suo "after"
        # scatta su una finestra distrutta ("application has been destroyed")
        bar = getattr(self, "_stop_ovl_bar", None)
        self._stop_ovl_bar = None
        if bar is not None:
            try:
                bar.stop()
            except tk.TclError:
                pass
        try:
            ovl.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ #
    # Riassunto / minute
    # ------------------------------------------------------------------ #
    def _on_summary(self) -> None:
        if self.state_ != "stopped" or self.generating:
            return
        self._generate("summary")

    def _on_minutes(self) -> None:
        if self.state_ != "stopped" or self.generating:
            return
        self._generate("minutes")

    def _on_screen(self) -> None:
        """Dialog registrazione schermo (non modale, resta in memoria)."""
        dlg = getattr(self, "_screen_dlg", None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            dlg.focus_force()
            return
        self._screen_dlg = ScreenDialog(
            self, on_area=lambda a: self._refresh_area_lbl())

    def _refresh_area_lbl(self) -> None:
        area = config.SETTINGS.get("screen_area") or {}
        mode = str(area.get("mode") or "screen")
        fps = int(area.get("fps") or 15)
        rect = area.get("rect")
        if mode == "window" and isinstance(rect, (list, tuple)):
            desc = f"finestra ({rect[2]}x{rect[3]})"
        elif mode == "region" and isinstance(rect, (list, tuple)):
            desc = f"regione ({rect[2]}x{rect[3]})"
        elif mode == "window":
            desc = "finestra — scegli «Area…»"
        else:
            desc = "tutto schermo (monitor virtuale)"
        self.screen_area_lbl.configure(
            text=(f"area video: {desc} · {fps} fps"
                  if self.video_var.get() else f"(area video: {desc})"))

    def _on_video_toggle(self) -> None:
        state = "normal" if self.video_var.get() else "disabled"
        try:
            self.area_btn.configure(state=state)
        except Exception:                       # noqa: BLE001
            pass
        self._refresh_area_lbl()

    def _resolve_screen_rect(self) -> tuple:
        area = config.SETTINGS.get("screen_area") or {}
        mode = str(area.get("mode") or "screen")
        rect = area.get("rect")
        if (mode in ("window", "region")
                and isinstance(rect, (list, tuple)) and len(rect) == 4):
            return tuple(int(v) for v in rect)
        import mss
        with mss.mss() as sct:
            mon = sct.monitors[0]
        return (int(mon["left"]), int(mon["top"]),
                int(mon["width"]), int(mon["height"]))

    def _start_screen_rec(self) -> None:
        rect = self._resolve_screen_rect()
        area = config.SETTINGS.get("screen_area") or {}
        fps = int(area.get("fps") or 15)
        raw = self.session_dir / "schermo_raw.mp4"
        self._screen_rec = screenrec.ScreenRecorder(
            rect, fps, raw, on_error=self._q_error)
        self._screen_rec.start()
        self._sys_line(
            f"registrazione video attiva ({rect[2]}x{rect[3]} @ {fps} fps) "
            "— il MP4 con audio incorporato si salva alla fine con "
            "«Salva video…» (o da solo se scade il timer)")

    def _stop_screen_rec(self, info: dict) -> None:
        """Ferma la cattura video: il file resta TEMPORANEO («Salva video…»)."""
        rec, self._screen_rec = self._screen_rec, None
        if rec is None:
            return
        try:
            video = rec.stop(timeout=60)
            if video is None:
                if rec.error:
                    info["warnings"].append(
                        f"registrazione video: {rec.error}")
                return
            info["video_temp"] = str(video)
        except Exception as exc:                # noqa: BLE001
            info["warnings"].append(f"fermata video: {exc}")

    def _on_context(self) -> None:
        """Contesto: una sola finestra, salvato e riutilizzato in futuro."""
        if self._context_dlg is not None and self._context_dlg.winfo_exists():
            self._context_dlg.lift()
            self._context_dlg.focus_force()
            return
        dlg = ContextDialog(
            self,
            notes=str(config.SETTINGS.get("context_notes", "")),
            folder=str(config.SETTINGS.get("context_folder", "")))
        self._context_dlg = dlg
        self.wait_window(dlg)
        self._context_dlg = None
        if dlg.result is None:
            return
        config.SETTINGS["context_notes"] = dlg.result["notes"]
        config.SETTINGS["context_folder"] = dlg.result["folder"] or ""
        config.save_settings()
        self.logger.info("contesto salvato: %d car, cartella=%s",
                         len(dlg.result["notes"]), dlg.result["folder"])
        extra = (f" · cartella: {dlg.result['folder']}"
                 if dlg.result["folder"] else "")
        self._sys_line(f"contesto salvato ({len(dlg.result['notes'])} "
                       f"caratteri){extra} — usato da Riassunto e Minute")
        self._refresh_context_panel()

    def _build_context(self) -> str:
        """Contesto per i prompt. Gira nel thread di generazione: nessuna
        chiamata Tk (l'analisi della cartella progetto può durare secondi
        e prima bloccava l'interfaccia)."""
        notes = str(config.SETTINGS.get("context_notes", "")).strip()
        folder = str(config.SETTINGS.get("context_folder", "")).strip()
        if not notes and not folder:
            return context_ai.context_placeholder()
        if not folder:
            return notes
        self.q.put(("status", "analisi del contesto…"))
        try:
            text = context_ai.build_project_context(Path(folder), notes)
            self.q.put(("sys", f"contesto progetto analizzato: {folder} "
                               f"({len(text)} caratteri)"))
            return text
        except Exception as exc:
            self.q.put(("sys", f"analisi contesto non riuscita: {exc}"))
            return notes or context_ai.context_placeholder()

    def _on_video_analysis(self) -> None:
        """Analisi visiva del video: frame -> descrizione (chiama l'AI)."""
        if self.state_ != "stopped" or self.generating:
            return
        if not self._has_video():
            messagebox.showinfo("Video non disponibile",
                                "Nessun video da analizzare: avvia una "
                                "sessione con «Con video» attiva.",
                                parent=self)
            return
        missing = config.missing_credentials()
        if missing:
            messagebox.showerror(
                "Configurazione incompleta",
                "Mancano i dati per l'IA:\n\n• " + "\n• ".join(missing),
                parent=self)
            self._open_settings()
            return
        self.generating = True
        self._update_controls()
        self._set_status("analisi del video in corso… (frame + AI)")
        self.configure(cursor="watch")
        self._open_viewer("video")
        threading.Thread(target=self._video_analysis_worker, daemon=True,
                         name="video-analysis").start()

    def _video_analysis_worker(self) -> None:
        try:
            text = service.analyze_video(
                config.SETTINGS, Path(self._video_temp),
                self.session_dir / "frames")
            self.video_analysis = text   # attributo non-Tk: ok dal thread
            self.q.put(("output", {"ok": True, "kind": "video",
                                   "text": text}))
        except Exception as exc:
            self.logger.exception("analisi video fallita")
            self.q.put(("output", {"ok": False, "kind": "video",
                                   "error": str(exc)}))

    def _generate(self, kind: str) -> None:
        missing = config.missing_credentials()
        if missing:
            messagebox.showerror(
                "Configurazione incompleta",
                "Mancano i dati per l'IA:\n\n• " + "\n• ".join(missing),
                parent=self)
            self._open_settings()
            return
        if not self._has_content():
            messagebox.showinfo("Nessun contenuto",
                                "Non c'è nessuna trascrizione da riassumere.",
                                parent=self)
            return
        visual_text = ""
        if self._has_video():
            doc = DOC_STEM.get(kind, "riassunto")
            if self.video_analysis:
                ask = (f"L'analisi visiva del video è già pronta "
                       f"({len(self.video_analysis)} caratteri).\n\n"
                       f"Incorporarla nel {doc}?")
            else:
                ask = ("Questa sessione ha un video.\n\n"
                       "Vuoi che l'AI analizzi anche ciò che è apparso a "
                       "schermo (codice, finestre, piattaforma della call, "
                       "chi appare evidenziato) oltre all'audio, per "
                       f"arricchire il {doc}?\n\n"
                       "Serve qualche secondo in più.")
            if messagebox.askyesno("Analisi visiva del video", ask,
                                   parent=self):
                visual_text = (self.video_analysis or "__GENERATE__")
        self.generating = True
        self._update_controls()
        self._set_status("generazione delle minute in corso…"
                         if kind == "minutes"
                         else "generazione riassunto in corso…")
        self.configure(cursor="watch")
        self._open_viewer(kind)
        threading.Thread(target=self._generate_worker,
                         args=(kind, None, visual_text),
                         daemon=True, name=f"gen-{kind}").start()

    def _open_viewer(self, kind: str) -> None:
        if self.viewer is None or not self.viewer.winfo_exists():
            self.viewer = ResultViewer(self)
        title = {"minutes": "Minute", "video": "Analisi video"}.get(
            kind, "Riassunto")
        self.viewer.prepare(kind, self.stamp,
                            self.out_dir_var.get().strip(), title)
        self.viewer.present()

    @staticmethod
    def _doc_prompts(kind: str, results: list, duration_s: float,
                     context_text: str, visual: str) -> tuple[str, str]:
        """(system, user) per riassunto o minute della sessione."""
        fields = dict(duration=outputs.format_ts(duration_s),
                      transcript=outputs.build_transcript_plain(results),
                      date=outputs.display_date(), context=context_text,
                      visual=visual)
        if kind == "minutes":
            return (prompts.MINUTES_SYSTEM,
                    prompts.MINUTES_USER.format(**fields))
        return prompts.SUMMARY_SYSTEM, prompts.SUMMARY_USER.format(**fields)

    def _generate_worker(self, kind: str, context_text: str | None,
                          visual_text: str = "") -> None:
        try:
            if context_text is None:
                context_text = self._build_context()
            if visual_text == "__GENERATE__":
                self.q.put(("status", "analisi visiva del video in corso…"))
                try:
                    text = service.analyze_video(
                        config.SETTINGS, Path(self._video_temp),
                        self.session_dir / "frames")
                    self.video_analysis = text
                    visual_text = text
                except Exception as exc:        # noqa: BLE001
                    self.logger.warning("analisi video saltata: %s", exc)
                    self.q.put(("status",
                                f"analisi video non disponibile: {exc}"))
                    visual_text = ""
            visual = (f"\n\n{visual_text}" if visual_text else
                       "(nessuna analisi video per questa sessione)")
            system, user = self._doc_prompts(
                kind, self.results, self.final_duration, context_text,
                visual)
            self.logger.info("generazione %s avviata", kind)
            text = service.complete(config.SETTINGS, system, user,
                                    on_status=self._q_status)
            self.q.put(("output", {"ok": True, "kind": kind, "text": text}))
        except Exception as exc:
            self.logger.exception("generazione %s fallita", kind)
            self.q.put(("output", {"ok": False, "kind": kind,
                                   "error": str(exc)}))

    def _on_savedone(self, res: dict) -> None:
        """Esito dell'operazione di salvataggio in background."""
        if self._save_dlg is not None:
            self._save_dlg.close()
            self._save_dlg = None
        self._save_cancel = None
        power.release()           # fine salvataggio automatico del timer
        kind = res.get("kind", "info")
        title = res.get("title") or ("Salvato" if kind != "error"
                                     else "Salvataggio non riuscito")
        msg = res.get("msg") or ""
        if kind != "error" and "annullato" not in title.lower():
            self._session_saved = True
        if kind == "error":
            messagebox.showerror(title, msg, parent=self)
        elif kind == "warning":
            messagebox.showwarning(title, msg, parent=self)
        else:
            messagebox.showinfo(title, msg, parent=self)
        # chiusura richiesta durante il salvataggio automatico del timer
        if self._destroy_after_stop and self.state_ == "stopped":
            self._cleanup_and_destroy()

    def _on_deferred(self) -> None:
        """Apri il dialog «Analisi file…» (riassunto/minute da file)."""
        if self._deferred_dlg is not None:
            try:
                if self._deferred_dlg.winfo_exists():
                    self._deferred_dlg.lift()
                    self._deferred_dlg.focus_force()
                    return
            except Exception:                   # noqa: BLE001
                pass
            self._deferred_dlg = None
        self._deferred_dlg = DeferredDialog(self, self._on_deferred_done)

    def _on_deferred_done(self, kind: str, text: str, stamp: str,
                          out_dir: str) -> None:
        """Mostra nella finestra di anteprima il documento generato dal file."""
        self.video_analysis = None
        if self.viewer is None or not self.viewer.winfo_exists():
            self.viewer = ResultViewer(self)
        title = "Minute" if kind == "minutes" else "Riassunto"
        self.viewer.prepare(kind, stamp, out_dir, f"{title} (da file)")
        self.viewer.set_content(text)
        self.viewer.present()
        self._sys_line(f"{title} generato dall'analisi differita "
                       f"(stampa {stamp}) — usa «Salva…» nel documento")

    # Loop eventi
    # ------------------------------------------------------------------ #
    def _poll(self) -> None:
        try:
            while True:
                try:
                    kind, payload = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(kind, payload)
                except Exception as exc:  # difesa: l'UI non deve morire
                    self.logger.exception("errore nel gestore eventi UI")
                    self._set_status(f"errore UI: {exc}")

            now = time.monotonic()
            if self.state_ == "running" and self.started_mono:
                dur = outputs.format_ts(now - self.started_mono)
                self._set_var(self.dur_var, dur)
                self._tick_autostop(now)
                if self.state_ == "running":
                    self._tick_callwatch(now)
            if self.state_ == "running":
                # pallino «REC» lampeggiante e durata nel titolo (visibile
                # anche dalla barra delle applicazioni a finestra ridotta)
                if self.fault_info is None:
                    dot = "●" if int(now * 1.6) % 2 == 0 else "○"
                    self._set_pill(f"{dot} IN ASCOLTO", RED)
                left = self._var_cache.get(str(self.timer_var), "")
                dur = self._var_cache.get(str(self.dur_var), "00:00")
                self._set_title(
                    f"● REC {dur}"
                    + (f" · {left.replace(chr(0x23f1), '').strip()}"
                       if left else "")
                    + f" — {self._base_title}")
            if self.pipeline is not None:
                self._set_var(self.api_var, f"API {self.pipeline.calls}")
                self._set_var(self.seg_var, f"segmenti {len(self.results)}")
                if self.state_ == "running" and self.pipeline.paused:
                    q = self.pipeline.queue.qsize()
                    n = len(self.pipeline.faults)
                    self._set_status(
                        f"trascrizione in pausa ({n} fermati, {q} in coda) — "
                        "la registrazione continua")
            if self.state_ == "stopping":
                q = (self.pipeline.queue.qsize()
                     if self.pipeline is not None else 0)
                msg = (f"chiusura… {q} segmenti ancora in elaborazione"
                       if q else "chiusura… ultimo aggiornamento in corso…")
                if msg != getattr(self, "_stop_msg", ""):
                    self._stop_msg = msg
                    self._set_status(msg)
                    self._update_stop_overlay(
                        ("Attendi: " + msg.split("… ", 1)[1])
                        if "… " in msg else "Attendi…")
            else:
                self._stop_msg = ""
        except Exception:  # difesa: un errore qui non deve fermare il pump
            self.logger.exception("aggiornamento UI fallito")
        finally:
            # sempre rischedulato: altrimenti durata, pill, contatori e
            # generazioni restano bloccati per sempre senza alcun errore
            try:
                if self.winfo_exists():
                    busy = (self.state_ in ("running", "stopping")
                            or self.generating or self._save_dlg is not None
                            or not self.q.empty())
                    self._poll_after = self.after(100 if busy else 200,
                                                  self._poll)
            except Exception:            # Tk in chiusura
                pass

    def _handle(self, kind: str, payload) -> None:
        if kind == "segment":
            self._render_segment(payload)
        elif kind == "level":
            # picco tra due aggiornamenti: con il throttling i colpi brevi
            # (parlato) non vanno persi
            if payload > self._sess_peak:
                self._sess_peak = payload
            self._check_volume(payload)
            if self._callwatch is not None and payload > self._VOL_SIGNAL:
                self._callwatch.sound(time.monotonic())
            v = min(100.0, payload * 350.0)
            self._level_peak = max(getattr(self, "_level_peak", 0.0), v)
            now = time.monotonic()
            if now - self._last_level_update >= 0.15:
                self._last_level_update = now
                self._show_level(self._level_peak)
                self._level_peak = 0.0
        elif kind == "sys":
            self._sys_line(str(payload))
        elif kind == "status":
            self._set_status(payload)
        elif kind == "error":
            self.logger.warning("errore pipeline: %s", payload)
            self._set_status(payload)
            self._sys_line(f"errore: {payload}", tag="err")
        elif kind == "fault":
            self._on_fault(payload)
        elif kind == "models":
            if self._fault_dlg is not None and self._fault_dlg.winfo_exists():
                self._fault_dlg.set_models(payload["models"],
                                           payload["current"])
        elif kind == "stopped":
            self._on_stopped(payload)
        elif kind == "output":
            self._on_output(payload)
        elif kind == "saveprog":
            if self._save_dlg is not None:
                try:
                    self._save_dlg.update_progress(
                        payload.get("p", 0.0), payload.get("msg") or None)
                except Exception:               # noqa: BLE001
                    pass
        elif kind == "savedone":
            self._on_savedone(payload)

    def _on_stopped(self, info: dict) -> None:
        self.state_ = "stopped"
        self._hide_stop_overlay()
        self.final_duration = float(info.get("duration", 0.0))
        self._set_var(self.dur_var, outputs.format_ts(self.final_duration))
        self.started_mono = None
        # prima di _update_controls: altrimenti «Salva video…» resta spento
        self._video_temp = str(info["video_temp"]) if info.get(
            "video_temp") else None
        self._show_level(0.0)
        self._set_var(self.timer_var, "")
        self._set_title(self._base_title)
        self._update_controls()
        self._set_status("sessione conclusa")
        n_seg = sum(1 for r in self.results
                    if (r.get("italian") or "").strip()
                    or (r.get("original") or "").strip())
        self._sys_line(
            f"sessione conclusa — durata "
            f"{outputs.format_ts(self.final_duration)} · "
            f"segmenti trascritti: {n_seg}")
        self.logger.info(
            "sessione conclusa durata=%.1fs segmenti=%d parlato=%d "
            "picco=%.4f", self.final_duration, n_seg, self._seg_count,
            self._sess_peak)
        no_speech = None
        if self._seg_count == 0 and self.final_duration >= 5:
            pct = min(100.0, self._sess_peak * 350.0)
            no_speech = (
                f"Nessun parlato rilevato (livello massimo {pct:.0f}%) su: "
                f"{self._sess_devices}. Verifica che l'audio della chiamata "
                "esca da questo dispositivo (o scegline un altro in «Audio "
                "di sistema») e che il volume non sia a zero.")
            self.logger.warning("nessun parlato rilevato: %s",
                                self._sess_devices)
            self._sys_line(no_speech, tag="err")
        if self._video_temp:
            self._sys_line("video pronto (temporaneo) — usa il pulsante "
                           "«Salva video…»")

        lines = [f"Durata: {outputs.format_ts(self.final_duration)}",
                 f"Segmenti trascritti: {n_seg}", ""]
        if self._autostop_fired:
            lines.insert(0, {
                "timer": "\u23f1 Fermata automatica dal timer.\n",
                "fine chiamata": "\U0001f4de Fermata a fine chiamata.\n",
            }.get(self._autostop_reason,
                  "\U0001f4be Arresto automatico attivo: salvataggio "
                  "automatico.\n"))
        if no_speech:
            lines.insert(0, f"⚠ {no_speech}\n")
        if self._has_video():
            lines.append("Video pronto: «Salva video…» (MP4 con audio)")
        if self._has_recording():
            lines.append("Registrazione audio: «Salva registrazione…»")
        if self._has_content():
            lines.append("Trascrizione: «Salva trascrizione…»")
        lines += ["",
                  "Nulla è stato salvato in automatico: usa i pulsanti "
                  "«Salva…», «Riassunto» e «Minute». I file temporanei "
                  "spariscono alla chiusura o alla prossima registrazione."]
        for w in info.get("warnings", []):
            lines.append(f"⚠ {w}")
        # timer scaduto: si salva da solo (l'utente può essere assente);
        # l'esito arriva nella finestra di fine salvataggio
        if self._autostop_fired and self._autosave_after_timer():
            # il PC resta sveglio fino a fine salvataggio (_on_savedone)
            for w in info.get("warnings", []):
                self._sys_line(w, tag="err")
            return
        power.release()
        messagebox.showinfo("Sessione conclusa", "\n".join(lines),
                            parent=self)

        if self._destroy_after_stop:
            self._cleanup_and_destroy()

    def _on_output(self, info: dict) -> None:
        self.generating = False
        self.configure(cursor="")
        self._update_controls()
        kind = info.get("kind") or "summary"
        if self.viewer is None or not self.viewer.winfo_exists():
            self._open_viewer(kind)
        if info.get("ok"):
            text = info.get("text") or ""
            self.logger.info("generazione completata (%s, %d car)", kind,
                             len(text))
            if len(text.strip()) < 20:
                # il provider risponde ma produce nulla di utile: senza
                # avviso l'utente vede solo un documento vuoto
                self.logger.warning("output %s quasi vuoto dal modello",
                                    kind)
                self._sys_line("attenzione: il modello ha risposto ma il "
                               "testo è quasi vuoto — riprova o cambia "
                               "modello in Configurazione", tag="err")
                messagebox.showwarning(
                    "Output quasi vuoto",
                    "Il modello ha risposto con un testo vuoto o quasi "
                    f"vuoto ({len(text.strip())} caratteri).\n\n"
                    "Riprova; se succede sempre cambia modello o "
                    "provider in Configurazione → Testo.", parent=self)
            self._set_status("risultato pronto — usa «Salva…» se vuoi")
            self.viewer.set_content(text)
        else:
            self.logger.error("generazione fallita: %s", info.get("error"))
            self._set_status("generazione fallita")
            self.viewer.set_error(str(info.get("error")))

    def _on_self_configure(self, event) -> None:
        if event.widget is not self:
            return
        if self.viewer is not None and self.viewer.winfo_exists():
            self.viewer.reposition()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _clear_text(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _insert(self, content: str, tag: str | None = None,
                stick: bool = True) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", content, tag)
        self.text.configure(state="disabled")
        if stick:
            self.text.see("end")

    def _sys_line(self, msg: str, tag: str = "sys") -> None:
        stick = self.text.yview()[1] >= 0.995
        self._insert(f"[sistema] {msg}\n", tag, stick)

    def _render_segment(self, r: dict) -> None:
        stick = self.text.yview()[1] >= 0.995
        ts = f"[{outputs.format_ts(r.get('start', 0))}] "
        self.text.configure(state="normal")
        if r.get("error"):
            self.text.insert("end", ts, "ts")
            self.text.insert("end",
                             f"errore trascrizione: {r['error']}\n", "err")
        else:
            italian = (r.get("italian") or "").strip()
            original = (r.get("original") or "").strip()
            if not italian and not original:
                self.text.configure(state="disabled")
                return
            lang = (r.get("language") or "?").strip()
            is_it = (not lang) or lang.lower().startswith("it")
            if not is_it:
                self._flag_lang_mismatch(lang)
            else:
                self._lang_mismatch = 0
            badge_it = "" if is_it else f"({lang}) "
            if not italian:
                self.text.insert("end", ts, "ts")
                self.text.insert("end", f"{badge_it}{original}\n", "orig")
            elif r.get("bilingual") and original and original != italian:
                self.text.insert("end", ts, "ts")
                self.text.insert("end", f"({lang}) {original}\n", "orig")
                self.text.insert("end", " " * len(ts) + f"→ {italian}\n",
                                 "it")
            else:
                self.text.insert("end", ts, "ts")
                self.text.insert("end", f"{badge_it}{italian}\n", "it")
        self.text.configure(state="disabled")
        if stick:
            self.text.see("end")

    def _flag_lang_mismatch(self, lang: str) -> None:
        """Avviso sul misdetection: con 3 segmenti non-italiani di fila
        suggerisce la lingua forzata (era il bug «italiano tradotto in
        spagnolo» con Whisper)."""
        self._lang_mismatch += 1
        if self._lang_mismatch == 3:
            self._lang_mismatch += 1        # once per session
            self._sys_line(
                f"lingua rilevata «{lang}» su più segmenti consecutivi: se "
                "il parlato era italiano, in Configurazione → Trascrizione "
                "metti «Lingua del parlato: Italiano (forzato)»")

    # ------------------------------------------------------------------ #
    # Varie
    # ------------------------------------------------------------------ #
    def _toggle_translation(self) -> None:
        config.SETTINGS["bilingual"] = bool(self.bilingual_var.get())
        config.save_settings()
        if self.pipeline is not None:
            self.pipeline.bilingual = self.bilingual_var.get()
        if self.state_ == "running":
            self._sys_line(
                "traduzione attivata (originale + italiano)"
                if self.bilingual_var.get()
                else "traduzione disattivata (solo italiano)")

    def _cleanup_and_destroy(self) -> None:
        power.release()
        self._stop_callwatch()
        # Ferma il batch writer per transcript_live.md
        self._tx_write_stop.set()
        self.hw.stop()
        try:
            self._tx_write_thread.join(timeout=2.0)
        except Exception:
            pass
        dlg = getattr(self, "_screen_dlg", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.destroy()
            except Exception:                   # noqa: BLE001
                self.logger.exception("chiusura dialog area video fallita")
            self._screen_dlg = None
        try:
            recorder.cleanup_session(self.session_dir)
        except Exception:
            self.logger.exception("pulizia sessione fallita")
        try:
            removed = config.purge_temp_sessions()
            self.logger.info("chiusura: %d voci temporanee rimosse", removed)
        except Exception:
            self.logger.exception("pulizia temporanee fallita")
        # annulla il poll prima della destroy: altrimenti Tk tenta di
        # richiamare "_poll" su un widget distrutto e scrive
        # "invalid command name ... _poll" su stderr
        try:
            self.after_cancel(self._poll_after)
        except Exception:                     # noqa: BLE001
            pass
        try:
            self.after_cancel(self._hw_after)
        except Exception:                     # noqa: BLE001
            pass
        self._hide_stop_overlay()
        self.destroy()
    def _hw_tick(self) -> None:
        """Aggiorna la barra CPU/GPU/RAM + il chip Whisper GPU/CPU (1 s)."""
        try:
            if not self.winfo_exists():
                return
            if self.wm_state() == "iconic":
                # ridotta a icona: niente campionamento né ridisegno
                self._hw_after = self.after(2000, self._hw_tick)
                return
            s = self.hw.snapshot()
            if s:
                self._set_var(self.hw_var, s)
            if config.SETTINGS.get("stt", {}).get("type") == "offline":
                from ..ai import offline
                d = offline.last_device()
                txt = f"Whisper {d}" if d else ""
                if txt != self._var_cache.get(str(self.dev_var)):
                    self._set_var(self.dev_var, txt)
                    self.dev_lbl.configure(fg=GREEN if d == "GPU" else MUTED)
            else:
                self._set_var(self.dev_var, "")
            self._hw_after = self.after(1000, self._hw_tick)
        except Exception:                     # noqa: BLE001
            pass

    def _on_close(self) -> None:
        self.logger.info("chiusura richiesta (stato=%s)", self.state_)
        if self.state_ == "running":
            if not messagebox.askyesno(
                    "Registrazione in corso",
                    "La registrazione è in corso: fermarla e uscire?",
                    parent=self):
                return
            self._destroy_after_stop = True
            self._on_stop()
            return
        if self.state_ == "stopping":
            self._destroy_after_stop = True
            self._set_status("attendo la fermata…")
            return
        if self._save_dlg is not None:
            # salvataggio in corso: i temporanei servono ancora
            self._destroy_after_stop = True
            self._set_status("attendo la fine del salvataggio…")
            return
        if (self.state_ == "stopped" and not self._session_saved
                and (self._has_recording() or self._has_video()
                     or self._has_content())):
            if not messagebox.askyesno(
                    "Registrazione non salvata",
                    "La registrazione dell'ultima sessione non è stata "
                    "salvata: chiudendo i file temporanei vengono "
                    "eliminati.\n\nUscire comunque?\n\n(«No» per tornare "
                    "e usare i pulsanti «Salva…»)",
                    icon="warning", default="no", parent=self):
                return
        self._cleanup_and_destroy()
