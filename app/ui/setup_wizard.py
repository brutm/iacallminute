"""Setup wizard for first-time configuration.

Guides the user through:
1. Transcription provider selection (Whisper offline / online API)
2. Whisper model download (if offline selected)
3. Text/summary provider selection (Gemini / GitHub Copilot / other)
4. API key configuration (if needed)
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from .. import config
from . import theme


class SetupWizard(tk.Toplevel):
    """Multi-step setup wizard dialog."""

    WHISPER_SIZES = {
        "tiny": ("Tiny", "~75 MB", "Fastest, lower accuracy"),
        "base": ("Base", "~150 MB", "Fast, decent accuracy"),
        "small": ("Small", "~500 MB", "Good balance (recommended)"),
        "medium": ("Medium", "~1.5 GB", "Better accuracy, slower"),
        "large-v3": ("Large v3", "~3 GB", "Best accuracy, slowest"),
    }

    def __init__(self, parent: tk.Tk | None = None, on_complete: Callable | None = None):
        super().__init__(parent)
        self.on_complete = on_complete
        self.result: dict = {}

        self.title("IACallMinute - Setup")
        self.geometry("600x500")
        self.resizable(False, False)
        self.configure(bg=theme.BG)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        if parent:
            self.transient(parent)
            self.grab_set()

        theme.apply_style(self)

        self._current_step = 0
        self._steps = [
            ("Welcome", self._build_welcome),
            ("Transcription", self._build_transcription),
            ("Whisper Download", self._build_whisper_download),
            ("Text Provider", self._build_text_provider),
            ("API Configuration", self._build_api_config),
            ("Complete", self._build_complete),
        ]

        self._stt_choice = tk.StringVar(value="offline")
        self._whisper_size = tk.StringVar(value="small")
        self._download_now = tk.BooleanVar(value=True)
        self._text_choice = tk.StringVar(value="gemini")
        self._api_key = tk.StringVar()

        self._main_frame = ttk.Frame(self, style="Card.TFrame")
        self._main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        self._nav_frame = ttk.Frame(self, style="TFrame")
        self._nav_frame.pack(fill="x", padx=20, pady=(0, 20))

        self._back_btn = ttk.Button(self._nav_frame, text="< Back", command=self._prev_step)
        self._back_btn.pack(side="left")

        self._next_btn = ttk.Button(self._nav_frame, text="Next >", command=self._next_step)
        self._next_btn.pack(side="right")

        self._show_step()

    def _clear_main(self):
        for w in self._main_frame.winfo_children():
            w.destroy()

    def _show_step(self):
        self._clear_main()
        title, builder = self._steps[self._current_step]

        ttk.Label(self._main_frame, text=title, font=theme.FONT_HEADER,
                  style="TLabel").pack(anchor="w", pady=(10, 20))

        builder()

        self._back_btn.config(state="normal" if self._current_step > 0 else "disabled")

        if self._current_step == len(self._steps) - 1:
            self._next_btn.config(text="Finish")
        elif self._current_step == 2 and self._stt_choice.get() != "offline":
            self._next_btn.config(text="Skip >")
        else:
            self._next_btn.config(text="Next >")

    def _next_step(self):
        if self._current_step == len(self._steps) - 1:
            self._finish()
            return

        if self._current_step == 1 and self._stt_choice.get() != "offline":
            self._current_step = 3
        elif self._current_step == 2 and self._download_now.get():
            if not self._start_download():
                return
        else:
            self._current_step += 1

        if self._current_step < len(self._steps):
            self._show_step()

    def _prev_step(self):
        if self._current_step > 0:
            if self._current_step == 3 and self._stt_choice.get() != "offline":
                self._current_step = 1
            else:
                self._current_step -= 1
            self._show_step()

    def _build_welcome(self):
        text = (
            "Welcome to IACallMinute!\n\n"
            "This wizard will help you configure the application for first use.\n\n"
            "You will choose:\n"
            "  - How to transcribe audio (offline or online)\n"
            "  - Which AI to use for summaries and minutes\n"
            "  - API keys (if using online services)\n\n"
            "You can change these settings later in the Configuration panel."
        )
        ttk.Label(self._main_frame, text=text, wraplength=540, justify="left",
                  style="TLabel").pack(anchor="w", fill="x")

    def _build_transcription(self):
        ttk.Label(self._main_frame, text="How do you want to transcribe audio?",
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        choices = ttk.Frame(self._main_frame, style="Card.TFrame")
        choices.pack(fill="x", pady=5)

        ttk.Radiobutton(
            choices, text="Whisper Offline (recommended)",
            variable=self._stt_choice, value="offline",
            command=self._on_stt_change
        ).pack(anchor="w", pady=5)
        ttk.Label(choices, text="    Runs locally, no internet needed, free, private",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(
            choices, text="Google Gemini (online)",
            variable=self._stt_choice, value="gemini",
            command=self._on_stt_change
        ).pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Requires API key, fast, good accuracy",
                  style="Muted.TLabel").pack(anchor="w")

        self._whisper_frame = ttk.LabelFrame(self._main_frame, text="Whisper Model Size",
                                              padding=10)
        self._whisper_frame.pack(fill="x", pady=(20, 0))

        for size, (name, disk, desc) in self.WHISPER_SIZES.items():
            f = ttk.Frame(self._whisper_frame, style="Card.TFrame")
            f.pack(fill="x", pady=2)
            ttk.Radiobutton(f, text=f"{name} ({disk})", variable=self._whisper_size,
                            value=size).pack(side="left")
            ttk.Label(f, text=f"  - {desc}", style="Muted.TLabel").pack(side="left")

        self._on_stt_change()

    def _on_stt_change(self):
        if self._stt_choice.get() == "offline":
            self._whisper_frame.pack(fill="x", pady=(20, 0))
        else:
            self._whisper_frame.pack_forget()

    def _build_whisper_download(self):
        size = self._whisper_size.get()
        name, disk, _ = self.WHISPER_SIZES[size]

        ttk.Label(self._main_frame,
                  text=f"Download Whisper {name} model ({disk})?",
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        ttk.Radiobutton(self._main_frame, text="Download now (recommended)",
                        variable=self._download_now, value=True).pack(anchor="w", pady=5)

        ttk.Radiobutton(self._main_frame, text="Download later (on first transcription)",
                        variable=self._download_now, value=False).pack(anchor="w", pady=5)

        ttk.Label(self._main_frame,
                  text="\nNote: The download may take a few minutes depending on your connection.",
                  style="Muted.TLabel", wraplength=540).pack(anchor="w", pady=(20, 0))

        self._progress_frame = ttk.Frame(self._main_frame, style="Card.TFrame")
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(self._progress_frame, variable=self._progress_var,
                                              maximum=100, length=500)
        self._progress_label = ttk.Label(self._progress_frame, text="", style="Muted.TLabel")

    def _start_download(self) -> bool:
        self._progress_frame.pack(fill="x", pady=(20, 0))
        self._progress_bar.pack(fill="x")
        self._progress_label.pack(anchor="w", pady=(5, 0))

        self._next_btn.config(state="disabled")
        self._back_btn.config(state="disabled")

        self._progress_label.config(text="Initializing download...")
        self._progress_var.set(0)
        self.update()

        def download_thread():
            try:
                size = self._whisper_size.get()
                self._download_whisper_model(size)
                self.after(0, self._download_complete)
            except Exception as e:
                self.after(0, lambda: self._download_error(str(e)))

        threading.Thread(target=download_thread, daemon=True).start()
        return False

    def _download_whisper_model(self, size: str):
        from huggingface_hub import snapshot_download

        model_id = f"Systran/faster-whisper-{size}"
        models_dir = config.APPDATA_DIR / "modelli" / "whisper" / size

        def progress_callback(progress: float):
            self.after(0, lambda: self._update_progress(progress))

        self.after(0, lambda: self._progress_label.config(text=f"Downloading {model_id}..."))

        snapshot_download(
            repo_id=model_id,
            local_dir=str(models_dir),
            local_dir_use_symlinks=False,
        )

        self.after(0, lambda: self._progress_var.set(100))

    def _update_progress(self, progress: float):
        self._progress_var.set(progress)
        self._progress_label.config(text=f"Downloading... {progress:.0f}%")

    def _download_complete(self):
        self._progress_label.config(text="Download complete!")
        self._next_btn.config(state="normal")
        self._back_btn.config(state="normal")
        self._current_step += 1
        self.after(500, self._show_step)

    def _download_error(self, error: str):
        self._progress_label.config(text=f"Error: {error}")
        self._next_btn.config(state="normal")
        self._back_btn.config(state="normal")
        messagebox.showerror("Download Error",
                             f"Failed to download model:\n{error}\n\n"
                             "You can try again later from the Configuration panel.")

    def _build_text_provider(self):
        ttk.Label(self._main_frame, text="Which AI should generate summaries and minutes?",
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        choices = ttk.Frame(self._main_frame, style="Card.TFrame")
        choices.pack(fill="x", pady=5)

        ttk.Radiobutton(
            choices, text="Google Gemini",
            variable=self._text_choice, value="gemini"
        ).pack(anchor="w", pady=5)
        ttk.Label(choices, text="    Free tier available, requires API key",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(
            choices, text="GitHub Copilot",
            variable=self._text_choice, value="copilot"
        ).pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Uses your existing Copilot subscription, login required",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(
            choices, text="Groq (fast, free tier)",
            variable=self._text_choice, value="groq"
        ).pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Very fast inference, requires API key",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(
            choices, text="Custom OpenAI-compatible endpoint",
            variable=self._text_choice, value="openai"
        ).pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Use any compatible API (OpenAI, local LLM, etc.)",
                  style="Muted.TLabel").pack(anchor="w")

    def _build_api_config(self):
        provider = self._text_choice.get()

        if provider == "copilot":
            ttk.Label(self._main_frame,
                      text="GitHub Copilot uses your browser login.",
                      style="TLabel").pack(anchor="w", pady=(0, 15))

            ttk.Label(self._main_frame,
                      text="You will be prompted to authenticate when generating your first summary.\n\n"
                           "Make sure you have an active GitHub Copilot subscription.",
                      style="Muted.TLabel", wraplength=540).pack(anchor="w")
            return

        labels = {
            "gemini": ("Google Gemini API Key", "Get one free at: aistudio.google.com/apikey"),
            "groq": ("Groq API Key", "Get one free at: console.groq.com/keys"),
            "openai": ("API Key (optional)", "Leave empty if your endpoint doesn't require one"),
        }

        label, hint = labels.get(provider, ("API Key", ""))

        ttk.Label(self._main_frame, text=label, style="TLabel").pack(anchor="w", pady=(0, 5))

        key_entry = ttk.Entry(self._main_frame, textvariable=self._api_key, width=60, show="*")
        key_entry.pack(anchor="w", pady=(0, 5))

        ttk.Label(self._main_frame, text=hint, style="Muted.TLabel").pack(anchor="w")

        if provider == "openai":
            ttk.Label(self._main_frame, text="\nEndpoint URL:", style="TLabel").pack(anchor="w", pady=(15, 5))
            self._api_url = tk.StringVar(value="https://api.openai.com/v1")
            ttk.Entry(self._main_frame, textvariable=self._api_url, width=60).pack(anchor="w")

    def _build_complete(self):
        ttk.Label(self._main_frame,
                  text="Setup complete!",
                  font=theme.FONT_HEADER, style="TLabel").pack(anchor="w", pady=(0, 20))

        summary = "Your configuration:\n\n"

        if self._stt_choice.get() == "offline":
            summary += f"  Transcription: Whisper {self._whisper_size.get()} (offline)\n"
        else:
            summary += "  Transcription: Google Gemini (online)\n"

        provider_names = {
            "gemini": "Google Gemini",
            "copilot": "GitHub Copilot",
            "groq": "Groq",
            "openai": "Custom OpenAI endpoint",
        }
        summary += f"  Summaries: {provider_names.get(self._text_choice.get(), 'Unknown')}\n"

        ttk.Label(self._main_frame, text=summary, style="TLabel", justify="left").pack(anchor="w")

        ttk.Label(self._main_frame,
                  text="\nYou can change these settings anytime in the Configuration panel.",
                  style="Muted.TLabel").pack(anchor="w", pady=(20, 0))

    def _finish(self):
        self.result = {
            "stt_type": self._stt_choice.get(),
            "stt_model": self._whisper_size.get() if self._stt_choice.get() == "offline" else "gemini",
            "text_type": self._text_choice.get(),
            "text_api_key": self._api_key.get(),
            "text_api_url": getattr(self, "_api_url", tk.StringVar()).get(),
        }

        self._apply_settings()

        if self.on_complete:
            self.on_complete(self.result)

        self.destroy()

    def _apply_settings(self):
        settings = config.SETTINGS

        if self.result["stt_type"] == "offline":
            settings["stt"] = {
                "type": "offline",
                "model": self.result["stt_model"],
                "api_key": "",
                "device": "auto",
            }
        else:
            settings["stt"] = {
                "type": "gemini",
                "model": "gemini-3.5-flash-lite",
                "api_key": self.result.get("text_api_key", ""),
            }

        text_type = self.result["text_type"]
        if text_type == "gemini":
            settings["text"] = {
                "type": "gemini",
                "model": "gemini-3.5-flash-lite",
                "api_key": self.result.get("text_api_key", ""),
            }
        elif text_type == "copilot":
            settings["text"] = {
                "type": "copilot",
                "model": "gpt-4o-mini",
                "api_key": "",
            }
        elif text_type == "groq":
            settings["text"] = {
                "type": "groq",
                "model": "llama-3.3-70b-versatile",
                "api_key": self.result.get("text_api_key", ""),
                "api_url": "https://api.groq.com/openai/v1",
            }
        else:
            settings["text"] = {
                "type": "openai",
                "model": "gpt-4o-mini",
                "api_key": self.result.get("text_api_key", ""),
                "api_url": self.result.get("text_api_url", "https://api.openai.com/v1"),
            }

        settings["setup_complete"] = True
        config.save_settings()

    def _on_cancel(self):
        if messagebox.askyesno("Cancel Setup",
                               "Are you sure you want to cancel?\n\n"
                               "You can run the setup wizard again from the Configuration panel."):
            self.destroy()


def needs_setup() -> bool:
    """Check if first-time setup is needed."""
    return not config.SETTINGS.get("setup_complete", False)


def run_setup(parent: tk.Tk | None = None, on_complete: Callable | None = None) -> SetupWizard:
    """Launch the setup wizard."""
    return SetupWizard(parent, on_complete)
