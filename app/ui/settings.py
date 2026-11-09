"""Finestra di configurazione: provider, modelli, chiavi, log."""
from __future__ import annotations

import logging
import os
import queue
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk

from .. import config
from ..ai import local_models, service
from ..storage import recorder
from .theme import (BG, BLUE, BORDER, CARD, FONT_MONO, FONT_SMALL, GREEN,
                    MUTED, RED, apply_style)
from .tkutil import SafeDestroy

log = logging.getLogger("iacm.ui.settings")

AI_STUDIO_KEY_URL = "https://aistudio.google.com/apikey"

HINT_FONT = ("Segoe UI", 9)
TITLE_FONT = ("Segoe UI Semibold", 11)


class SettingsDialog(SafeDestroy, tk.Toplevel):
    """Configurazione completa dell'app (persistita in settings.json)."""

    def __init__(self, parent: tk.Misc, on_saved=None):
        super().__init__(parent)
        self.title("Configurazione — IACallMinute")
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.on_saved = on_saved
        self.result = False

        apply_style(self)

        # --- variabili da settings correnti -----------------------------
        s = config.SETTINGS
        self.log_var = tk.BooleanVar(value=bool(s.get("log_enabled", False)))
        stt = s["stt"]
        self.stt_type = tk.StringVar(value=stt.get("type", "gemini"))
        self.stt_key = tk.StringVar(value=stt.get("api_key", ""))
        self.stt_model = tk.StringVar(value=stt.get("model", ""))
        self.stt_base = tk.StringVar(value=stt.get("base_url", ""))
        self.stt_lang = tk.StringVar(
            value=stt.get("language", "auto") or "auto")
        self.stt_device = tk.StringVar(
            value=stt.get("device", "auto") or "auto")

        txt = s["text"]
        self.text_type = tk.StringVar(value=txt.get("type", "gemini"))
        self.text_key = tk.StringVar(value=txt.get("api_key", ""))
        self.text_model = tk.StringVar(value=txt.get("model", ""))
        self.text_base = tk.StringVar(value=txt.get("base_url", ""))

        self._key_widgets: dict = {}
        self._model_hints: dict = {}
        # selezione modelli: combobox editabile per provider
        self._model_combo: dict[str, ttk.Combobox] = {}
        self._model_lists: dict[str, list[str]] = {}
        self._model_list_pending: set[str] = set()
        self._model_apply_wait: set[str] = set()
        self.after(300, self._model_tick)
        self._stt_hints = {
            "gemini": ("es. gemini-3.5-flash-lite \u2014 trascrizione e "
                       "traduzione via API"),
            "openai": ("es. whisper-large-v3, distil-whisper\u2026 "
                       "(serve endpoint)"),
            "offline": ("small (460 MB, consigliato \u2014 bilanciato e in "
                        "tempo reale) \u00b7 medium (1,5 GB, ~3\u00d7 pi\u00f9 "
                        "lento su CPU) \u00b7 large-v3 (3 GB). Al primo uso "
                        "il modello viene scaricato una volta (~1-2 min), "
                        "poi funziona senza rete."),
        }

        self._build()
        self._toggle_types()
        self.after(20, self._set_dev_status)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.grab_set()
        self.after(50, self._center)

    # ------------------------------------------------------------------ #
    def _center(self) -> None:
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, padx=14, pady=12)
        outer.grid(row=0, column=0, sticky="nsew")

        nb = ttk.Notebook(outer)
        nb.grid(row=0, column=0, sticky="nsew")

        nb.add(self._tab_general(nb), text=" Generale ")
        nb.add(self._tab_stt(nb), text=" Trascrizione (audio) ")
        nb.add(self._tab_text(nb), text=" Testo (riassunto, minute, traduzione) ")
        nb.add(self._tab_local(nb), text=" Modelli locali ")

        btns = ttk.Frame(outer, style="Card.TFrame")
        btns.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.test_btn = ttk.Button(btns, text="🔌  Test connessione",
                                   command=self._test)
        self.test_btn.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Annulla", command=self._cancel).grid(
            row=0, column=1, padx=4)
        ttk.Button(btns, text="Salva", style="Accent.TButton",
                   command=self._save).grid(row=0, column=2, padx=4)

    # --- widget di riga -------------------------------------------------- #
    def _label(self, parent, text: str, row: int, col_span: int = 3,
               font=FONT_SMALL, fg=MUTED) -> None:
        tk.Label(parent, text=text, bg=CARD, fg=fg, font=font).grid(
            row=row, column=0, columnspan=col_span, sticky="w")

    @staticmethod
    def _opts(w) -> dict:
        return {k: v for k, v in w.grid_info().items() if k != "in"}

    @staticmethod
    def _grid_show(items, show: bool) -> None:
        """Mostra/nasconde (widget, opts) ricordando il layout originale."""
        for w, opts in items:
            if show:
                w.grid(**opts)
            else:
                w.grid_remove()

    def _key_row(self, parent, var: tk.StringVar, hint: str, row: int,
                 extra_btn: tk.Widget | None = None,
                 tag: str = "") -> int:
        """label (row) + entry/mostra (row+1) + hint (row+2). Ritorna row+3."""
        made = []
        lab = tk.Label(parent, text="Chiave API", bg=CARD, fg=MUTED,
                       font=FONT_SMALL)
        lab.grid(row=row, column=0, sticky="w")
        made.append(lab)
        if extra_btn is not None:
            extra_btn.grid(row=row, column=2, sticky="e")
            made.append(extra_btn)
        entry = ttk.Entry(parent, textvariable=var, show="*")
        entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew",
                   pady=(2, 0))
        made.append(entry)
        eye = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(
            parent, text="mostra", variable=eye,
            command=lambda: entry.configure(show="" if eye.get() else "*"))
        chk.grid(row=row + 1, column=2, padx=(6, 0), sticky="n")
        made.append(chk)
        hint_lab = tk.Label(parent, text=hint, bg=CARD, fg=MUTED,
                            font=HINT_FONT, justify="left")
        hint_lab.grid(row=row + 2, column=0, columnspan=3, sticky="w")
        made.append(hint_lab)
        if tag:
            self._key_widgets[tag] = [(w, self._opts(w)) for w in made]
        return row + 3

    def _model_row(self, parent, var: tk.StringVar, hint: str,
                   row: int, tag: str = "") -> int:
        """label (row) + combobox (row+1) + hint (row+2). Ritorna row+3.

        La combobox e' editabile: si puo' scegliere dall'elenco del
        provider o scrivere a mano il nome esatto del modello.
        """
        self._label(parent, "Modello", row)
        cb = ttk.Combobox(parent, textvariable=var)   # normal = editabile
        cb.grid(row=row + 1, column=0, columnspan=2, sticky="ew",
                pady=(2, 0))
        if tag:
            self._model_combo[tag] = cb
            cb.configure(postcommand=lambda t=tag: self._fill_models(t))
        hint_lab = tk.Label(parent, text=hint, bg=CARD, fg=MUTED,
                            font=HINT_FONT, justify="left")
        hint_lab.grid(row=row + 2, column=0, columnspan=3, sticky="w")
        if tag:
            self._model_hints[tag] = hint_lab
        return row + 3

    # --- elenco modelli del provider (riempi la combobox) ---------------- #
    def _fill_models(self, tag: str) -> None:
        """Elenco modelli del provider corrente (in thread, mai blocca UI)."""
        cb = self._model_combo.get(tag)
        if cb is None:
            return
        typ = (self.text_type.get() if tag == "text"
               else self.stt_type.get())
        cfg = self._model_probe_cfg(tag)
        self._fill_models_probe(cb, typ, cfg, p_key := f"{tag}:{typ}", tag)

    def _fill_models_probe(self, cb, typ: str, cfg: dict,
                           cache_key: str, tag: str) -> None:
        out = self._model_lists.get(cache_key)
        if out is not None:
            self._apply_model_values(cb, out)
            return
        # probe in thread (reti lente: la dropdown resta reattiva)
        if cache_key in self._model_list_pending:
            return
        self._model_list_pending.add(cache_key)

        def worker() -> None:
            try:
                lst = self._fetch_provider_models(typ, cfg)
            except Exception:                     # noqa: BLE001
                lst = []
            self._model_lists[cache_key] = lst
            self._model_list_pending.discard(cache_key)
            try:
                self.after(0, lambda: self._apply_model_values(cb, lst))
            except RuntimeError:
                self._model_apply_wait.add(cache_key)
                # il tick lo riprende (Tk senza mainloop / finestra chiusa)

        threading.Thread(target=worker, daemon=True,
                         name=f"models-{tag}").start()

    def _model_tick(self) -> None:
        """Applica gli elenchi modelli preparati dai worker di rete."""
        try:
            for key in list(self._model_apply_wait):
                cb = self._model_combo.get(key.split(":", 1)[0])
                lst = self._model_lists.get(key)
                if cb is None or lst is None:
                    self._model_apply_wait.discard(key)
                    continue
                self._apply_model_values(cb, lst)
                self._model_apply_wait.discard(key)
            self.after(300, self._model_tick)
        except tk.TclError:
            pass     # dialog distrutta: il ciclo si spegne

    def _model_probe_cfg(self, tag: str) -> dict:
        """Copia i valori dei campi (thread UI) per il worker di rete."""
        if tag == "text":
            return {"type": self.text_type.get(),
                    "api_key": self.text_key.get(),
                    "base_url": self.text_base.get(),
                    "model": self.text_model.get()}
        return {"type": self.stt_type.get(),
                "api_key": self.stt_key.get(),
                "base_url": self.stt_base.get(),
                "model": self.stt_model.get()}

    @staticmethod
    def _fetch_provider_models(typ: str, cfg: dict) -> list[str]:
        """Elenco modelli: SOLO io di rete/file, mai widget Tk."""
        if typ == "offline":
            return list(config.OFFLINE_SIZES)
        if typ == "gemini":
            from ..ai import service
            return service.available_models()
        if typ == "copilot":
            from ..ai import copilot
            return copilot.list_models() or [copilot.DEFAULT_MODEL]
        return SettingsDialog._openai_models(cfg)

    def _provider_models(self, tag: str) -> list[str]:
        """Elenco dei modelli per il tag, in base al provider selezionato."""
        if tag == "stt":
            typ = self.stt_type.get()
            if typ == "offline":
                return list(config.OFFLINE_SIZES)
            if typ == "gemini":
                from ..ai import service
                return service.available_models()
            return self._openai_models({
                "api_key": self.stt_key.get(), "base_url": self.stt_base.get()})
        from ..ai import copilot
        if self.text_type.get() == "copilot":
            return copilot.list_models() or [copilot.DEFAULT_MODEL]
        if self.text_type.get() == "gemini":
            from ..ai import service
            return service.available_models()
        return self._openai_models({
            "api_key": self.text_key.get(), "base_url": self.text_base.get()})

    @staticmethod
    def _openai_models(cfg: dict) -> list[str]:
        """GET {base}/models su endpoint OpenAI-compat; [] se non risponde."""
        import requests as _rq
        base = (cfg.get("base_url") or "").strip().rstrip("/")
        if not base:
            return []
        headers = {}
        key = (cfg.get("api_key") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        names: list[str] = []
        try:
            resp = _rq.get(f"{base}/models", headers=headers, timeout=8)
            if resp.ok:
                data = resp.json()
                items = data.get("data", data) if isinstance(data, dict) else data
                for m in items or []:
                    mid = m.get("id") if isinstance(m, dict) else str(m)
                    if mid:
                        names.append(str(mid))
        except Exception:                          # noqa: BLE001
            return []
        return sorted(names)

    def _apply_model_values(self, cb, values: list[str]) -> None:
        """Tecnica: i widget elder della dialog possono essere distrutti."""
        try:
            if not cb.winfo_exists():
                return
            # il valore corrente resta sempre selezionabile/visibile
            cur = cb.get()
            uniq = list(dict.fromkeys(values))
            if cur and cur not in uniq:
                uniq = [cur] + uniq
            cb.configure(values=tuple(uniq))
        except tk.TclError:
            pass

    # --- tab: generale -------------------------------------------------- #
    def _tab_general(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=0)

        r = 0
        self._label(f, "Cartelle, traduzione e salvataggi si gestiscono "
                       "nella finestra principale (destinazione, "
                       "temporanea, pulsanti «Salva…»).", r, font=HINT_FONT)
        r += 2

        ttk.Checkbutton(
            f, text="Salva il log su file — di default SCONSPIRATO "
                    "(nessun file, nessun spazio occupato)",
            variable=self.log_var).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 8))
        r += 1

        ff = recorder.ffmpeg_path()
        tk.Label(
            f, text=("ffmpeg: OK — conversione MP3 attiva" if ff else
                     "ffmpeg: ASSENTE — il pulsante «Salva registrazione…» "
                     "salverà un WAV"),
            bg=CARD, fg=(GREEN if ff else RED), font=FONT_MONO).grid(
            row=r, column=0, columnspan=2, sticky="w")
        r += 2

        row = ttk.Frame(f, style="Card.TFrame")
        row.grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Button(row, text="Apri cartella log", command=self._open_logs).grid(
            row=0, column=0, padx=(0, 6))
        ttk.Button(row, text="Pulisci temporanee ora",
                   command=self._purge_now).grid(row=0, column=1, padx=6)
        return f

    # --- tab: trascrizione ---------------------------------------------- #
    def _tab_stt(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        f.columnconfigure(0, weight=1, uniform="stt")
        f.columnconfigure(1, weight=1, uniform="stt")
        f.columnconfigure(2, weight=0)

        r = 0
        self._label(f, "Provider di trascrizione (audio → testo)", r,
                    font=TITLE_FONT, fg=BLUE)
        r += 1
        radios = ttk.Frame(f, style="Card.TFrame")
        radios.grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Radiobutton(radios, text="Google Gemini (audio inline)",
                        variable=self.stt_type, value="gemini",
                        command=self._toggle_types).grid(row=0, column=0,
                                                         padx=(0, 14))
        ttk.Radiobutton(radios, text="Endpoint OpenAI-compatibile (Whisper)",
                        variable=self.stt_type, value="openai",
                        command=self._toggle_types).grid(row=0, column=1)
        ttk.Radiobutton(radios,
                        text="Offline — Whisper locale (nessuna quota, "
                             "funziona anche senza rete)",
                        variable=self.stt_type, value="offline",
                        command=self._toggle_types).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        r += 1

        # lingua della trascrizione: comune a tutti i provider
        lang_row = ttk.Frame(f, style="Card.TFrame")
        lang_row.grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 4))
        tk.Label(lang_row, text="Lingua del parlato:", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, padx=(0, 6))
        lang_vals = [v for v, _ in config.STT_LANGS]
        lang_menu = ttk.OptionMenu(
            lang_row, self.stt_lang, self.stt_lang.get(), *lang_vals)
        lang_menu.grid(row=0, column=1, sticky="w")
        tk.Label(lang_row,
                 text="(«Rilevamento automatico» funziona quasi sempre; "
                      "se l'italiano viene etichettato male e poi "
                      "«tradotto» male, forza «Italiano»)",
                  bg=CARD, fg=MUTED, font=HINT_FONT, justify="left"
                  ).grid(row=0, column=2, padx=(10, 0))
        r += 1

        # dispositivo di calcolo Whisper: GPU se rilevata, CPU altrimenti
        dev_row = ttk.Frame(f, style="Card.TFrame")
        dev_row.grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 4))
        tk.Label(dev_row, text="Dispositivo di calcolo:", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, padx=(0, 6))
        ttk.OptionMenu(dev_row, self.stt_device, self.stt_device.get(),
                       *[v for v, _ in config.STT_DEVICES]).grid(
            row=0, column=1, sticky="w")
        self._dev_status = tk.Label(
            dev_row,
            text="(usato solo dal provider Offline; «Automatico» usa la "
                 "GPU quando c'e', altrimenti la CPU)",
            bg=CARD, fg=MUTED, font=HINT_FONT, justify="left")
        self._dev_status.grid(row=0, column=2, padx=(10, 0))
        r += 1

        ai_btn = ttk.Button(f, text="Chiave gratuita → AI Studio",
                            command=lambda: webbrowser.open(AI_STUDIO_KEY_URL))
        r = self._key_row(
            f, self.stt_key,
            "Vuota = chiave rilevata automaticamente "
            "(auth opencode / variabile GEMINI_API_KEY)",
            r, extra_btn=ai_btn, tag="stt")
        r = self._model_row(
            f, self.stt_model, self._stt_hints["gemini"], r, tag="stt")

        self.stt_base_frame = tk.Frame(f, bg=CARD)
        self.stt_base_frame.columnconfigure(0, weight=1)
        self.stt_base_frame.grid(row=r, column=0, columnspan=3, sticky="ew",
                                 pady=(4, 0))
        self._stt_base_opts = self._opts(self.stt_base_frame)
        tk.Label(self.stt_base_frame, text="URL endpoint (base, senza path)",
                 bg=CARD, fg=MUTED, font=FONT_SMALL).grid(
            row=0, column=0, sticky="w")
        ttk.Entry(self.stt_base_frame, textvariable=self.stt_base).grid(
            row=1, column=0, sticky="ew", pady=(2, 0))
        tk.Label(self.stt_base_frame,
                 text="es. https://api.groq.com/openai/v1 · "
                      "http://localhost:1234/v1 (LM Studio)",
                 bg=CARD, fg=MUTED, font=HINT_FONT).grid(
            row=2, column=0, sticky="w")
        r += 1

        self._stt_end_hint = tk.Label(
            f, text="L'endpoint deve esporre POST /audio/transcriptions "
                    "(Whisper-compat). Serve solo testo? usa il tab «Testo».",
            bg=CARD, fg=MUTED, font=HINT_FONT, justify="left")
        self._stt_end_hint.grid(row=r + 1, column=0, columnspan=3,
                                sticky="w")
        self._stt_end_opts = self._opts(self._stt_end_hint)
        return f

    # --- tab: testo ------------------------------------------------------ #
    def _tab_text(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        f.columnconfigure(0, weight=1, uniform="txt")
        f.columnconfigure(1, weight=1, uniform="txt")
        f.columnconfigure(2, weight=0)

        r = 0
        self._label(f, "Provider di testo (riassunto, minute, traduzione)",
                    r, font=TITLE_FONT, fg=BLUE)
        r += 1
        radios = ttk.Frame(f, style="Card.TFrame")
        radios.grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Radiobutton(radios, text="Google Gemini", variable=self.text_type,
                        value="gemini",
                        command=self._on_text_type_change).grid(
            row=0, column=0, padx=(0, 14))
        ttk.Radiobutton(radios, text="Router OpenAI-compatibile "
                                     "(chat/completions)",
                        variable=self.text_type, value="openai",
                        command=self._on_text_type_change).grid(row=0, column=1)
        ttk.Radiobutton(radios, text="GitHub Copilot",
                        variable=self.text_type, value="copilot",
                        command=self._on_text_type_change).grid(
            row=0, column=2, padx=(14, 0))
        r += 1

        # preset pronti: un click riempie tipo, URL e modello
        pre = ttk.Frame(f, style="Card.TFrame")
        pre.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 4))
        tk.Label(pre, text="Preset:", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, padx=(0, 6))
        for i, (pkey, p) in enumerate(config.TEXT_PRESETS.items(), start=1):
            ttk.Button(pre, text=p["label"],
                       command=lambda k=pkey: self._apply_text_preset(k)
                       ).grid(row=0, column=i, padx=(0, 6))
        r += 1
        self._preset_hint = tk.Label(f, text="", bg=CARD, fg=MUTED,
                                     font=HINT_FONT, justify="left")
        self._preset_hint.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        r = self._key_row(
            f, self.text_key,
            "Vuota = usa la chiave della trascrizione (Gemini)", r,
            tag="text")
        # GitHub Copilot: riga con stato login + pulsante device flow
        cr = ttk.Frame(f, style="Card.TFrame")
        self._copilot_frame = cr
        cr.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self._copilot_status = tk.Label(
            cr, text="", bg=CARD, fg=MUTED, font=FONT_SMALL)
        self._copilot_status.grid(row=0, column=0, sticky="w")
        self._copilot_login_btn = ttk.Button(
            cr, text="🪪  Accedi con GitHub Copilot",
            command=self._copilot_login)
        self._copilot_login_btn.grid(row=0, column=1, sticky="e")
        self._copilot_opts = self._opts(cr)
        r += 1
        r = self._model_row(
            f, self.text_model,
            "es. gemini-3.5-flash-lite · per router: "
            "deepseek/deepseek-chat-v3.1, openai/gpt-4o-mini…", r,
            tag="text")

        self.text_base_frame = tk.Frame(f, bg=CARD)
        self.text_base_frame.columnconfigure(0, weight=1)
        self.text_base_frame.grid(row=r, column=0, columnspan=3, sticky="ew",
                                  pady=(4, 0))
        self._text_base_opts = self._opts(self.text_base_frame)
        tk.Label(self.text_base_frame, text="URL endpoint (base, senza path)",
                 bg=CARD, fg=MUTED, font=FONT_SMALL).grid(
            row=0, column=0, sticky="w")
        ttk.Entry(self.text_base_frame, textvariable=self.text_base).grid(
            row=1, column=0, sticky="ew", pady=(2, 0))
        tk.Label(self.text_base_frame,
                 text="es. https://openrouter.ai/api/v1 · "
                      "https://api.groq.com/openai/v1",
                 bg=CARD, fg=MUTED, font=HINT_FONT).grid(
            row=2, column=0, sticky="w")
        r += 1

        self._label(
            f, "Usato anche per tradurre i segmenti quando il provider "
               "di trascrizione non è Gemini.", r + 1, font=HINT_FONT)
        return f

    # --- tab: modelli locali --------------------------------------------- #
    def _tab_local(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=0)
        f.columnconfigure(2, weight=0)

        self._label(f, "Modelli locali (Whisper + Argos)", 0,
                    font=TITLE_FONT, fg=BLUE)
        r = 2

        # --- Whisper --------------------------------------------------- #
        tk.Label(f, text="Modelli Whisper (trascrizione offline)",
                 bg=CARD, fg=BLUE, font=("Segoe UI Semibold", 10),
                 anchor="w").grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1

        self._whisper_rows: dict[str, dict] = {}
        self._whisper_state: dict[str, dict] = {}
        for name in ("tiny", "base", "small", "medium", "large-v3"):
            row = ttk.Frame(f, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=2)
            row.columnconfigure(1, weight=1)
            tk.Label(row, text=name, bg=CARD, font=FONT_MONO,
                     fg=BLUE, width=10).grid(row=0, column=0, sticky="w")
            self._whisper_rows[name] = {
                "frame": row,
                "status": tk.Label(row, text="…", bg=CARD,
                                   fg=MUTED, font=FONT_SMALL,
                                   anchor="w"),
                "btn_dl": ttk.Button(row, text="Scarica",
                                     command=lambda n=name: self._dl_whisper(n)),
                "btn_del": ttk.Button(row, text="Elimina",
                                      command=lambda n=name: self._del_whisper(n)),
            }
            self._whisper_rows[name]["status"].grid(
                row=0, column=1, sticky="ew", padx=(8, 4))
            self._whisper_rows[name]["btn_del"].grid(row=0, column=3, padx=2)
            self._whisper_rows[name]["btn_dl"].grid(row=0, column=2, padx=2)
            r += 1

        r += 1

        # --- Argos ----------------------------------------------------- #
        tk.Label(f, text="Pacchetti traduzione Argos (lingua → italiano)",
                 bg=CARD, fg=BLUE, font=("Segoe UI Semibold", 10),
                 anchor="w").grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1

        self._argos_rows: dict[str, dict] = {}
        self._argos_state: dict[str, dict] = {}
        for code, label in local_models.ARGOS_LANGS:
            row = ttk.Frame(f, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=2)
            row.columnconfigure(1, weight=1)
            tk.Label(row, text=code.upper(), bg=CARD, font=FONT_MONO,
                     fg=BLUE, width=10).grid(row=0, column=0, sticky="w")
            self._argos_rows[f"{code}_it"] = {
                "frame": row,
                "status": tk.Label(row, text="…", bg=CARD,
                                   fg=MUTED, font=FONT_SMALL,
                                   anchor="w"),
                "btn_dl": ttk.Button(row, text="Scarica",
                                     command=lambda c=code: self._dl_argos(c)),
                "btn_del": ttk.Button(row, text="Elimina",
                                      command=lambda c=code: self._del_argos(c)),
            }
            self._argos_rows[f"{code}_it"]["status"].grid(
                row=0, column=1, sticky="ew", padx=(8, 4))
            self._argos_rows[f"{code}_it"]["btn_del"].grid(row=0, column=3, padx=2)
            self._argos_rows[f"{code}_it"]["btn_dl"].grid(row=0, column=2, padx=2)
            r += 1

        foot = tk.Frame(f, bg=CARD)
        foot.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        tk.Label(foot, text=f"I modelli sono in {local_models.MODELS_DIR}, "
                            f"nella cartella dell'applicazione.",
                 bg=CARD, fg=MUTED, font=HINT_FONT, justify="left").grid(
            row=0, column=0, sticky="w")
        ttk.Button(foot, text="Apri cartella modelli",
                   command=self._open_models_dir).grid(
            row=0, column=1, padx=(8, 0), sticky="e")

        r += 2

        # --- DLL CUDA (GPU opzionale) ---------------------------------- #
        tk.Label(f, text="Librerie CUDA (accelerazione GPU Whisper)",
                 bg=CARD, fg=BLUE, font=("Segoe UI Semibold", 10),
                 anchor="w").grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1
        row = ttk.Frame(f, style="Card.TFrame")
        row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=2)
        row.columnconfigure(1, weight=1)
        tk.Label(row, text="CUDA", bg=CARD, font=FONT_MONO,
                 fg=BLUE, width=10).grid(row=0, column=0, sticky="w")
        self._cuda_state: dict = {}
        self._cuda_status = tk.Label(row, text="…", bg=CARD, fg=MUTED,
                                     font=FONT_SMALL, anchor="w")
        self._cuda_status.grid(row=0, column=1, sticky="ew", padx=(8, 4))
        self._cuda_del = ttk.Button(row, text="Elimina",
                                    command=self._del_cuda)
        self._cuda_dl = ttk.Button(row, text="Scarica",
                                   command=self._dl_cuda)
        self._cuda_del.grid(row=0, column=3, padx=2)
        self._cuda_dl.grid(row=0, column=2, padx=2)

        self.after(80, self._refresh_local_models)
        return f

    def _open_models_dir(self) -> None:
        try:
            local_models.ensure_dirs()
            os.startfile(str(local_models.MODELS_DIR))
        except Exception as exc:                # noqa: BLE001
            messagebox.showerror(
                "Cartella modelli", str(exc), parent=self)

    def _refresh_local_models(self) -> None:
        # whisper e argos condividono lo stesso formato di riga: un solo
        # refresh per entrambi evita di tenere due copie del codice
        self._refresh_rows(local_models.list_whisper(), self._whisper_rows,
                           self._whisper_state)
        self._refresh_rows(local_models.list_argos(), self._argos_rows,
                           self._argos_state)
        self._refresh_cuda_row()

    @staticmethod
    def _refresh_rows(items, rows, state) -> None:
        for m in items:
            row = rows.get(m.id)
            if not row:
                continue
            st = state.setdefault(m.id, {"busy": False, "p": 0.0,
                                         "err": ""})
            if st["busy"]:
                txt = f"download in corso… {int(st['p'] * 100)}%"
                fg = MUTED
                row["btn_dl"].configure(state="disabled")
                row["btn_del"].configure(state="disabled")
            elif m.installed:
                txt = f"installato — {m.size_bytes / 1e6:.0f} MB"
                fg = GREEN
                row["btn_dl"].configure(state="disabled")
                row["btn_del"].configure(state="normal")
            else:
                txt = f"non installato — download stimato ≈{m.hint_mb} MB"
                fg = MUTED
                row["btn_dl"].configure(state="normal")
                row["btn_del"].configure(state="disabled")
            if st.get("err"):
                txt = f"errore: {st['err']}"
                fg = RED
            row["status"].configure(text=txt, fg=fg)

    def _refresh_cuda_row(self) -> None:
        st = self._cuda_state.setdefault(
            "cuda", {"busy": False, "p": 0.0, "err": ""})
        present = local_models.cuda_dll_present()
        if st["busy"]:
            txt = f"download in corso… {int(st['p'] * 100)}%"
            fg = MUTED
            self._cuda_dl.configure(state="disabled")
            self._cuda_del.configure(state="disabled")
        elif st.get("err"):
            txt = f"errore: {st['err']}"
            fg = RED
            self._cuda_dl.configure(state="normal")
            self._cuda_del.configure(state="disabled")
        elif present:
            txt = (f"installate ({local_models.CUDA_DLL_DIR}) — usate con "
                   "«Dispositivo: Automatico»")
            fg = GREEN
            self._cuda_dl.configure(state="disabled")
            self._cuda_del.configure(state="normal")
        else:
            txt = (f"non installate — «Automatico» usa la CPU; download "
                   f"stimato ≈{local_models._CUDA_SIZE_HINT_MB} MB "
                   "(nessuna installazione di sistema)")
            fg = MUTED
            self._cuda_dl.configure(state="normal")
            self._cuda_del.configure(state="disabled")
        self._cuda_status.configure(text=txt, fg=fg)

    def _dl_cuda(self) -> None:
        log.info("download DLL CUDA in corso…")
        self._start_dl(
            self._cuda_state, "cuda", "Download DLL CUDA fallito",
            lambda on_progress, on_done: local_models.download_cuda_dlls(
                on_progress=on_progress, on_done=on_done))

    def _set_dev_status(self) -> None:
        """Mostra accanto al selettore lo stato GPU rilevato (pigro)."""
        try:
            from ..ai import offline
            self._dev_status.configure(text=offline.gpu_info())
        except Exception:                   # noqa: BLE001
            pass

    def _del_cuda(self) -> None:
        self._ask_delete(
            "Elimina librerie CUDA",
            "Eliminare le librerie CUDA da modelli/cuda_dlls?\n"
            "La trascrizione ripartira' sulla CPU (nessun danno); "
            "potrai riscaricarle in seguito.",
            "DLL CUDA rimosse",
            local_models.delete_cuda_dlls)

    def _dl_whisper(self, name: str) -> None:
        log.info("download whisper %s in corso…", name)
        self._start_dl(
            self._whisper_state, name, "Download fallito",
            lambda on_progress, on_done: local_models.download_whisper(
                name, on_progress=on_progress, on_done=on_done))

    def _dl_argos(self, code: str) -> None:
        pair = f"{code}_it"
        log.info("download argos %s in corso…", pair)
        self._start_dl(
            self._argos_state, pair, "Installazione fallita",
            lambda on_progress, on_done: local_models.download_argos(
                pair, on_progress=on_progress, on_done=on_done))

    def _start_dl(self, state, key, err_title, start) -> None:
        st = state.setdefault(key, {"busy": False, "p": 0.0, "err": ""})
        st["busy"] = True
        st["err"] = ""
        self._refresh_local_models()
        # i callback arrivano da un thread: tornano nel thread Tk con after
        start(
            on_progress=lambda p, msg: self.after(
                0, self._set_progress, state, key, p, msg),
            on_done=lambda ok, msg: self.after(
                0, self._set_done, state, key, ok, msg, err_title),
        )

    def _set_progress(self, state, key, p: float, msg: str) -> None:
        st = state.setdefault(key, {"busy": False, "p": 0.0, "err": ""})
        st["busy"] = True
        st["p"] = p
        self._refresh_local_models()

    def _set_done(self, state, key, ok: bool, msg: str,
                  err_title: str) -> None:
        log.info("%s: %s (%s)", key, "ok" if ok else "fail", msg)
        st = state.setdefault(key, {"busy": False, "p": 0.0, "err": ""})
        st["busy"] = False
        st["err"] = "" if ok else msg
        self._refresh_local_models()
        if not ok:
            messagebox.showerror(err_title, msg, parent=self)

    def _del_whisper(self, name: str) -> None:
        self._ask_delete(
            "Elimina modello",
            f"Eliminare il modello Whisper '{name}' dalla cache?\n"
            f"Potrai riscaricarlo in seguito.",
            f"whisper {name} rimosso",
            lambda: local_models.delete_whisper(name))

    def _del_argos(self, code: str) -> None:
        pair = f"{code}_it"
        self._ask_delete(
            "Elimina pacchetto",
            f"Eliminare il pacchetto Argos '{pair}'?\n"
            f"Potrai reinstallarlo in seguito.",
            f"argos {pair} rimosso",
            lambda: local_models.delete_argos(pair))

    def _ask_delete(self, title, body, log_msg, do_delete) -> None:
        if not messagebox.askyesno(title, body, parent=self):
            return
        do_delete()
        log.info(log_msg)
        self._refresh_local_models()

    # ------------------------------------------------------------------ #
    def _toggle_types(self) -> None:
        st = self.stt_type.get()
        if st == "openai":
            self.stt_base_frame.grid(**self._stt_base_opts)
        else:
            self.stt_base_frame.grid_remove()
        self._grid_show(self._key_widgets.get("stt", []), st != "offline")
        if st == "offline":
            if (self.stt_model.get() or "").strip() not in config.OFFLINE_SIZES:
                self.stt_model.set(config.DEFAULT_OFFLINE_MODEL)
        elif (self.stt_model.get() or "").strip() in config.OFFLINE_SIZES:
            self.stt_model.set(config.DEFAULT_MODEL)
        hint = self._model_hints.get("stt")
        if hint is not None:
            hint.configure(text=self._stt_hints.get(st, ""))
        if st == "openai":
            self._stt_end_hint.grid(**self._stt_end_opts)
        else:
            self._stt_end_hint.grid_remove()
        cop = self.text_type.get() == "copilot"
        if cop or self.text_type.get() == "gemini":
            self.text_base_frame.grid_remove()
        else:
            self.text_base_frame.grid(**self._text_base_opts)
        self._grid_show([(self._copilot_frame, self._copilot_opts)], cop)
        if cop:
            self._refresh_copilot_status()

    def _refresh_copilot_status(self) -> None:
        from ..ai import copilot
        if copilot.is_logged_in():
            self._copilot_status.configure(
                text="✓ collegato (copilot.json) — account GitHub già "
                     "autorizzato", fg=GREEN)
            self._copilot_login_btn.configure(text="↻  Riconnetti")
        else:
            self._copilot_status.configure(
                text="non collegato — serve il login una volta", fg=MUTED)
            self._copilot_login_btn.configure(
                text="🪪  Accedi con GitHub Copilot")

    def _copilot_login(self) -> None:
        """Device flow GitHub in thread: apre il browser col codice."""
        from ..ai import copilot
        try:
            code, uri = copilot.start_login()
        except Exception as exc:                 # noqa: BLE001
            messagebox.showerror(
                "GitHub Copilot", f"Login non avviabile:\n{exc}", parent=self)
            return
        log.info("device flow copilot: codice=%s", code)
        webbrowser.open(uri)
        self._copilot_status.configure(
            text=f"attendere: inserisci il codice {code} nel browser "
                 "(github.com/login/device)", fg=BLUE)
        self._copilot_login_btn.configure(state="disabled")
        self._login_out = queue.Queue()
        threading.Thread(
            target=self._copilot_login_worker, args=(code,),
            daemon=True, name="copilot-login").start()
        self.after(200, self._poll_copilot_login)

    def _copilot_login_worker(self, code: str) -> None:
        from ..ai import copilot
        try:
            copilot.finish_login(code)
            self._login_out.put(("ok", None))
        except Exception as exc:                 # noqa: BLE001
            self._login_out.put(("err", str(exc)))

    def _poll_copilot_login(self) -> None:
        try:
            if not self.winfo_exists():
                return
            kind, payload = self._login_out.get_nowait()
        except queue.Empty:
            self.after(400, self._poll_copilot_login)
            return
        except tk.TclError:                    # finestra distrutta
            return
        self._copilot_login_btn.configure(state="normal")
        if kind == "ok":
            messagebox.showinfo(
                "GitHub Copilot",
                "Collegamento riuscito: da ora riassunto e minute usano "
                "GitHub Copilot (modello in Testo).", parent=self)
        else:
            messagebox.showerror(
                "GitHub Copilot", f"Login non completato:\n{payload}",
                parent=self)
        self._refresh_copilot_status()

    def _on_text_type_change(self) -> None:
        """Cambiando radio i campi si riempiono da soli.

        - Gemini: azzera l'URL endpoint e, se il modello era uno da router
          (contiene «/»), torna al modello Gemini predefinito.
        - Router OpenAI-compat: se l'endpoint e' vuoto parte dal preset Groq.
        - GitHub Copilot: nessun URL/chiave, modello economico predefinito.
        Poi applica la visibilita' dei campi (_toggle_types).
        """
        from ..ai import copilot
        if self.text_type.get() == "copilot":
            model = (self.text_model.get() or "").strip()
            # un vecchio modello Gemini/da-router non esiste su Copilot:
            # lo rimpiazzo col default economico (gpt-4o-mini)
            if (not model or "/" in model
                    or model.startswith(("gemini", "gpt-oss"))
                    or model.startswith(("mistral", "grok"))):
                self.text_model.set(copilot.DEFAULT_MODEL)
        elif self.text_type.get() == "gemini":
            self.text_base.set("")
            model = (self.text_model.get() or "").strip()
            # modelli di altri provider (gpt-4o-mini di Copilot, nomi da
            # router OpenAI, «models/…») non esistono su Google API:
            # torno al modello Gemini predefinito
            if (not model or "/" in model
                    or not model.startswith("gemini")):
                self.text_model.set(config.DEFAULT_MODEL)
        else:
            if not (self.text_base.get() or "").strip():
                p = config.TEXT_PRESETS["groq"]
                self.text_base.set(p["base_url"])
                model = (self.text_model.get() or "").strip()
                if not model or "/" not in model:
                    self.text_model.set(p["model"])
        self._toggle_types()

    def _apply_text_preset(self, key: str) -> None:
        """Un click dal preset: tipo, URL endpoint e modello del provider."""
        p = config.TEXT_PRESETS[key]
        self.text_type.set(p["type"])
        self.text_base.set(p["base_url"])
        self.text_model.set(p["model"])
        self._toggle_types()
        self._preset_hint.configure(text=p["hint"])

    # ------------------------------------------------------------------ #
    def _open_logs(self) -> None:
        try:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(config.LOG_DIR))
        except Exception as exc:
            messagebox.showerror("Log", f"Apertura cartella log fallita:\n{exc}",
                                 parent=self)

    def _purge_now(self) -> None:
        try:
            root = config.get_temp_root()
            removed = config.purge_temp_sessions(root)
            messagebox.showinfo(
                "Pulizia temporanee",
                f"Rimosse {removed} voci temporanee dalla cartella.",
                parent=self)
        except Exception as exc:
            messagebox.showerror("Pulizia temporanee", str(exc), parent=self)

    # ------------------------------------------------------------------ #
    def _collect(self) -> dict:
        s = config.SETTINGS
        stt_type = self.stt_type.get()
        stt_model = self.stt_model.get().strip()
        # Valida modello offline: se non è in OFFLINE_SIZES, usa il default
        if stt_type == "offline" and stt_model not in config.OFFLINE_SIZES:
            stt_model = config.DEFAULT_OFFLINE_MODEL
        text_type = self.text_type.get()
        text_model = self.text_model.get().strip()
        text_base = self.text_base.get().strip()
        lang = self.stt_lang.get().strip().lower() or "auto"
        valid_langs = {v for v, _ in config.STT_LANGS}
        if lang not in valid_langs:
            lang = "auto"
        device = self.stt_device.get().strip().lower() or "auto"
        if device not in {v for v, _ in config.STT_DEVICES}:
            device = "auto"
        # Gemini non usa endpoint: un URL lasciato dentro verrebbe salvato
        # e riusato al primo ritorno al router (config fuorviante)
        if text_type == "copilot":
            p = config.TEXT_PRESETS["copilot"]
            text_model = text_model or p["model"]
            text_base = p["base_url"]
        elif text_type != "gemini":
            if not text_model or "/" not in text_model:
                # nome non da router (es. rimasto un modello Gemini)
                text_model = config.TEXT_PRESETS["groq"]["model"]
        else:
            text_base = ""
            if not text_model or "/" in text_model:
                text_model = config.DEFAULT_MODEL
        return {
            "temp_root": s.get("temp_root") or str(config.get_temp_root()),
            "output_dir": s.get("output_dir", ""),
            "bilingual": bool(s.get("bilingual", True)),
            "log_enabled": bool(self.log_var.get()),
            "context_notes": s.get("context_notes", ""),
            "context_folder": s.get("context_folder", ""),
            "stt": {
                "type": stt_type,
                "api_key": self.stt_key.get().strip(),
                "model": stt_model,
                "base_url": self.stt_base.get().strip(),
                "language": lang,
                "device": device,
            },
            "text": {
                "type": text_type,
                "api_key": self.text_key.get().strip(),
                "model": text_model,
                "base_url": text_base,
            },
        }

    def _test(self) -> None:
        cand = self._collect()
        missing = self._missing_for(cand)
        if missing:
            messagebox.showwarning("Configurazione incompleta",
                                   "\n".join(missing), parent=self)
            return
        self.configure(cursor="watch")
        self.test_btn.configure(state="disabled")
        self.update_idletasks()
        log.info("test connessione in corso…")
        # Due chiamate di rete non devono bloccare il thread dell'UI:
        # mentre il worker risponde, la finestra resta reattiva.
        self._test_out: queue.Queue = queue.Queue()
        threading.Thread(target=self._test_worker, args=(cand,),
                         daemon=True, name="settings-test").start()
        self.after(100, self._poll_test)

    def _test_worker(self, cand: dict) -> None:
        try:
            self._test_out.put(("ok", service.test_settings(cand)))
        except Exception as exc:
            self._test_out.put(("err", exc))

    def _poll_test(self) -> None:
        try:
            if not self.winfo_exists():        # dialogo chiuso durante il test
                return
            try:
                kind, payload = self._test_out.get_nowait()
            except queue.Empty:
                self.after(100, self._poll_test)
                return
            self.configure(cursor="")
            self.test_btn.configure(state="normal")
            if kind == "ok":
                log.info("test connessione ok: %s", payload)
                messagebox.showinfo("Test riuscito", payload, parent=self)
            else:
                log.warning("test connessione fallito: %s", payload)
                messagebox.showerror("Test fallito", str(payload), parent=self)
        except tk.TclError:                    # finestra distrutta
            return

    @staticmethod
    def _missing_for(cfg: dict) -> list[str]:
        return config.missing_credentials(cfg)

    def _save(self) -> None:
        cand = self._collect()
        missing = self._missing_for(cand)
        if missing and not messagebox.askyesno(
                "Configurazione incompleta",
                "Mancano alcune impostazioni:\n\n" + "\n".join(missing) +
                "\n\nSalvarlo comunque?", parent=self):
            return
        # Aggiornamento in place: con clear() + update() un thread che legge
        # SETTINGS nella finestra tra le due chiamate vedrebbe un dict vuoto.
        for key in list(config.SETTINGS):
            if key not in cand:
                del config.SETTINGS[key]
        config.SETTINGS.update(cand)
        config.save_settings()
        config.setup_logging()      # applica subito on/off del log
        self.result = True
        log.info("settings salvati: stt=%s/%s text=%s/%s",
                 cand["stt"]["type"], cand["stt"]["model"],
                 cand["text"]["type"], cand["text"]["model"])
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                log.exception("callback on_saved fallita")
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()
