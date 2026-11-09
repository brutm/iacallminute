#!/usr/bin/env python3
"""IACallMinute Setup Installer.

Standalone installer that:
1. Guides through initial configuration
2. Downloads required models (Whisper)
3. Creates desktop shortcut (optional)
4. Saves initial settings

Compile with: pyinstaller --onefile --windowed --name IACallMinute_Setup installer.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import zipfile
import shutil
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "IACallMinute"
APP_VERSION = "1.1.1"

APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME

WHISPER_SIZES = {
    "tiny": ("Tiny", "~75 MB", "Fastest, lower accuracy"),
    "base": ("Base", "~150 MB", "Fast, decent accuracy"),
    "small": ("Small", "~500 MB", "Good balance (recommended)"),
    "medium": ("Medium", "~1.5 GB", "Better accuracy, slower"),
    "large-v3": ("Large v3", "~3 GB", "Best accuracy, slowest"),
}

BG = "#f1f5f9"
CARD = "#ffffff"
INK = "#0f172a"
MUTED = "#64748b"
BLUE = "#2563eb"
HEADER = "#0f172a"

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
FONT_HEADER = ("Segoe UI Semibold", 15)
FONT_SMALL = ("Segoe UI", 9)


def apply_style(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=BG)
    style.configure(".", font=FONT, background=BG, foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=CARD, foreground=INK, font=FONT)
    style.configure("Bg.TLabel", background=BG, foreground=INK, font=FONT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=FONT_SMALL)
    style.configure("Header.TLabel", background=HEADER, foreground="#f8fafc", font=FONT_HEADER)
    return style


class InstallerWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Setup")
        self.geometry("650x550")
        self.resizable(False, False)
        self.configure(bg=BG)

        apply_style(self)

        self._current_step = 0
        self._steps = [
            ("Welcome", self._build_welcome),
            ("Install Location", self._build_location),
            ("Transcription", self._build_transcription),
            ("Download Models", self._build_download),
            ("Text Provider", self._build_text_provider),
            ("API Key", self._build_api_config),
            ("Installing", self._build_installing),
            ("Complete", self._build_complete),
        ]

        self._install_dir = tk.StringVar(value=str(Path.home() / "AppData" / "Local" / APP_NAME))
        self._stt_choice = tk.StringVar(value="offline")
        self._whisper_size = tk.StringVar(value="small")
        self._download_now = tk.BooleanVar(value=True)
        self._text_choice = tk.StringVar(value="gemini")
        self._api_key = tk.StringVar()
        self._api_url = tk.StringVar(value="https://api.openai.com/v1")
        self._create_shortcut = tk.BooleanVar(value=True)

        self._header = ttk.Frame(self, style="TFrame")
        self._header.pack(fill="x")
        ttk.Label(self._header, text=f"{APP_NAME} Setup", font=("Segoe UI Semibold", 18),
                  background=HEADER, foreground="#ffffff", padding=(20, 15)).pack(fill="x")

        self._main_frame = ttk.Frame(self, style="Card.TFrame", padding=20)
        self._main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self._nav_frame = ttk.Frame(self, style="TFrame", padding=(20, 10))
        self._nav_frame.pack(fill="x")

        self._back_btn = ttk.Button(self._nav_frame, text="< Back", command=self._prev_step)
        self._back_btn.pack(side="left")

        self._cancel_btn = ttk.Button(self._nav_frame, text="Cancel", command=self._on_cancel)
        self._cancel_btn.pack(side="left", padx=(10, 0))

        self._next_btn = ttk.Button(self._nav_frame, text="Next >", command=self._next_step)
        self._next_btn.pack(side="right")

        self._recreate_config = True
        if _existing_installation():
            self._recreate_config = messagebox.askyesno(
                "Existing installation",
                "An existing installation/configuration was found.\n\n"
                "Do you want to recreate the configuration?\n\n"
                "Yes: run the full wizard and answer the configuration questions.\n"
                "No: keep the existing configuration and install directly.",
            )
        if not self._recreate_config:
            self._current_step = 6

        self._show_step()

        if not self._recreate_config:
            self.after(100, self._run_install)

    def _clear_main(self):
        for w in self._main_frame.winfo_children():
            w.destroy()

    def _show_step(self):
        self._clear_main()
        title, builder = self._steps[self._current_step]

        ttk.Label(self._main_frame, text=title, font=FONT_HEADER,
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        builder()

        self._back_btn.config(state="normal" if (0 < self._current_step < 6) else "disabled")

        if self._current_step == len(self._steps) - 1:
            self._next_btn.config(text="Finish", state="normal")
        elif self._current_step == 6:
            self._next_btn.config(state="disabled")
        else:
            self._next_btn.config(text="Next >", state="normal")

    def _next_step(self):
        if self._current_step == len(self._steps) - 1:
            self.destroy()
            return

        if self._current_step == 2 and self._stt_choice.get() != "offline":
            self._current_step = 4
        elif self._current_step == 3 and not self._download_now.get():
            self._current_step = 4
        elif self._current_step == 4 and self._text_choice.get() == "copilot":
            self._current_step = 6
        else:
            self._current_step += 1

        if self._current_step < len(self._steps):
            self._show_step()

        if self._current_step == 6:
            self.after(100, self._run_install)

    def _prev_step(self):
        if self._current_step > 0:
            if self._current_step == 4 and self._stt_choice.get() != "offline":
                self._current_step = 2
            elif self._current_step == 6 and self._text_choice.get() == "copilot":
                self._current_step = 4
            else:
                self._current_step -= 1
            self._show_step()

    def _build_welcome(self):
        text = (
            f"Welcome to {APP_NAME}!\n\n"
            "This wizard will help you install and configure the application.\n\n"
            "You will choose:\n"
            "  - Where to install the application\n"
            "  - How to transcribe audio (offline or online)\n"
            "  - Which AI to use for summaries and minutes\n"
            "  - API keys (if using online services)\n\n"
            "Click Next to continue."
        )
        ttk.Label(self._main_frame, text=text, wraplength=580, justify="left",
                  style="TLabel").pack(anchor="w", fill="x")

    def _build_location(self):
        ttk.Label(self._main_frame, text="Choose where to install IACallMinute:",
                  style="TLabel").pack(anchor="w", pady=(0, 10))

        loc_frame = ttk.Frame(self._main_frame, style="Card.TFrame")
        loc_frame.pack(fill="x", pady=5)

        ttk.Entry(loc_frame, textvariable=self._install_dir, width=60).pack(side="left", padx=(0, 10))
        ttk.Button(loc_frame, text="Browse...", command=self._browse_location).pack(side="left")

        ttk.Label(self._main_frame,
                  text=f"\nSettings and logs will be stored in:\n{APPDATA_DIR}",
                  style="Muted.TLabel").pack(anchor="w", pady=(20, 0))

        ttk.Checkbutton(self._main_frame, text="Create desktop shortcut",
                        variable=self._create_shortcut).pack(anchor="w", pady=(20, 0))

    def _browse_location(self):
        path = filedialog.askdirectory(initialdir=self._install_dir.get())
        if path:
            self._install_dir.set(path)

    def _build_transcription(self):
        ttk.Label(self._main_frame, text="How do you want to transcribe audio?",
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        choices = ttk.Frame(self._main_frame, style="Card.TFrame")
        choices.pack(fill="x", pady=5)

        ttk.Radiobutton(choices, text="Whisper Offline (recommended)",
                        variable=self._stt_choice, value="offline",
                        command=self._on_stt_change).pack(anchor="w", pady=5)
        ttk.Label(choices, text="    Runs locally, no internet needed, free, private",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(choices, text="Google Gemini (online)",
                        variable=self._stt_choice, value="gemini",
                        command=self._on_stt_change).pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Requires API key, fast, good accuracy",
                  style="Muted.TLabel").pack(anchor="w")

        self._whisper_frame = ttk.LabelFrame(self._main_frame, text="Whisper Model Size", padding=10)
        self._whisper_frame.pack(fill="x", pady=(20, 0))

        for size, (name, disk, desc) in WHISPER_SIZES.items():
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

    def _build_download(self):
        size = self._whisper_size.get()
        name, disk, _ = WHISPER_SIZES[size]

        ttk.Label(self._main_frame,
                  text=f"Download Whisper {name} model ({disk})?",
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        ttk.Radiobutton(self._main_frame, text="Download now during installation",
                        variable=self._download_now, value=True).pack(anchor="w", pady=5)

        ttk.Radiobutton(self._main_frame, text="Download later (on first use)",
                        variable=self._download_now, value=False).pack(anchor="w", pady=5)

        ttk.Label(self._main_frame,
                  text="\nNote: The download may take several minutes depending on your connection.\n"
                       "You can always download additional models later.",
                  style="Muted.TLabel", wraplength=580).pack(anchor="w", pady=(20, 0))

    def _build_text_provider(self):
        ttk.Label(self._main_frame, text="Which AI should generate summaries and minutes?",
                  style="TLabel").pack(anchor="w", pady=(0, 15))

        choices = ttk.Frame(self._main_frame, style="Card.TFrame")
        choices.pack(fill="x", pady=5)

        ttk.Radiobutton(choices, text="Google Gemini",
                        variable=self._text_choice, value="gemini").pack(anchor="w", pady=5)
        ttk.Label(choices, text="    Free tier available, requires API key from aistudio.google.com",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(choices, text="GitHub Copilot",
                        variable=self._text_choice, value="copilot").pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Uses your existing Copilot subscription, browser login",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(choices, text="Groq (fast, free tier)",
                        variable=self._text_choice, value="groq").pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Very fast inference, requires API key from console.groq.com",
                  style="Muted.TLabel").pack(anchor="w")

        ttk.Radiobutton(choices, text="Custom OpenAI-compatible endpoint",
                        variable=self._text_choice, value="openai").pack(anchor="w", pady=(15, 5))
        ttk.Label(choices, text="    Use any compatible API (OpenAI, local LLM, etc.)",
                  style="Muted.TLabel").pack(anchor="w")

    def _build_api_config(self):
        provider = self._text_choice.get()

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
            ttk.Entry(self._main_frame, textvariable=self._api_url, width=60).pack(anchor="w")

        ttk.Label(self._main_frame,
                  text="\nYou can change this later in the Configuration panel.",
                  style="Muted.TLabel").pack(anchor="w", pady=(20, 0))

    def _build_installing(self):
        ttk.Label(self._main_frame, text="Please wait while installing...",
                  style="TLabel").pack(anchor="w", pady=(0, 20))

        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(self._main_frame, variable=self._progress_var,
                                              maximum=100, length=560)
        self._progress_bar.pack(fill="x", pady=(0, 10))

        self._status_label = ttk.Label(self._main_frame, text="Preparing...", style="Muted.TLabel")
        self._status_label.pack(anchor="w")

        self._log_text = tk.Text(self._main_frame, height=10, width=70,
                                  font=("Consolas", 9), state="disabled")
        self._log_text.pack(fill="both", expand=True, pady=(15, 0))

    def _build_complete(self):
        ttk.Label(self._main_frame, text="Installation complete!",
                  style="TLabel").pack(anchor="w", pady=(0, 20))

        if self._recreate_config:
            summary = f"IACallMinute has been installed to:\n{self._install_dir.get()}\n\n"

            if self._stt_choice.get() == "offline":
                summary += f"Transcription: Whisper {self._whisper_size.get()} (offline)\n"
            else:
                summary += "Transcription: Google Gemini (online)\n"

            provider_names = {"gemini": "Google Gemini", "copilot": "GitHub Copilot",
                              "groq": "Groq", "openai": "Custom endpoint"}
            summary += f"Summaries: {provider_names.get(self._text_choice.get(), 'Unknown')}\n"
        else:
            summary = (f"IACallMinute has been installed to:\n{self._install_dir.get()}\n\n"
                       "Existing configuration kept unchanged.\n")

        ttk.Label(self._main_frame, text=summary, style="TLabel", justify="left").pack(anchor="w")

        ttk.Label(self._main_frame,
                  text="You can change these settings anytime in the Configuration panel.\n\n"
                       "Click Finish to close the installer.",
                  style="Muted.TLabel").pack(anchor="w", pady=(20, 0))

    def _log(self, msg: str):
        self._log_text.config(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.config(state="disabled")
        self.update()

    def _run_install(self):
        def install_thread():
            try:
                self._do_install()
                self.after(0, self._install_complete)
            except Exception as e:
                self.after(0, lambda: self._install_error(str(e)))

        threading.Thread(target=install_thread, daemon=True).start()

    def _do_install(self):
        install_dir = Path(self._install_dir.get())

        self.after(0, lambda: self._status_label.config(text="Creating directories..."))
        self.after(0, lambda: self._log("Creating installation directory..."))
        install_dir.mkdir(parents=True, exist_ok=True)
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        self.after(0, lambda: self._progress_var.set(5))

        self.after(0, lambda: self._status_label.config(text="Copying application..."))
        self.after(0, lambda: self._log("Copying IACallMinute.exe..."))
        self._copy_app(install_dir)
        self.after(0, lambda: self._progress_var.set(10))

        if self._recreate_config and self._stt_choice.get() == "offline" and self._download_now.get():
            self.after(0, lambda: self._status_label.config(text="Downloading Whisper model..."))
            self.after(0, lambda: self._log("Downloading Whisper model (this may take a while)..."))
            self._download_whisper()
            self.after(0, lambda: self._progress_var.set(70))

        if self._recreate_config:
            self.after(0, lambda: self._status_label.config(text="Saving configuration..."))
            self.after(0, lambda: self._log("Saving configuration..."))
            self._save_settings()
        else:
            self.after(0, lambda: self._log("Keeping existing configuration."))
        self.after(0, lambda: self._progress_var.set(80))

        if self._create_shortcut.get():
            self.after(0, lambda: self._status_label.config(text="Creating shortcut..."))
            self.after(0, lambda: self._log("Creating desktop shortcut..."))
            self._create_desktop_shortcut()
        self.after(0, lambda: self._progress_var.set(90))

        self.after(0, lambda: self._status_label.config(text="Finalizing..."))
        self.after(0, lambda: self._log("Installation complete!"))
        self.after(0, lambda: self._progress_var.set(100))

    def _copy_app(self, install_dir: Path):
        candidates = []
        if getattr(sys, "frozen", False):
            candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "IACallMinute.exe")
        for p in [Path.cwd(), Path(__file__).resolve().parent]:
            candidates.append(p / "IACallMinute.exe")
            candidates.append(p / "dist" / "IACallMinute.exe")
        src = None
        for c in candidates:
            try:
                if c and c.exists():
                    src = c
                    break
            except Exception:
                pass
        if src is None:
            self.after(0, lambda: self._log("Warning: IACallMinute.exe not found; install manually."))
            return
        dst = install_dir / "IACallMinute.exe"
        try:
            shutil.copy2(src, dst)
            self.after(0, lambda: self._log(f"Copied {src} -> {dst}"))
        except Exception as e:
            self.after(0, lambda: self._log(f"Warning: could not copy exe: {e}"))

    def _download_whisper(self):
        try:
            from huggingface_hub import snapshot_download

            size = self._whisper_size.get()
            model_id = f"Systran/faster-whisper-{size}"
            models_dir = APPDATA_DIR / "modelli" / "whisper" / size

            self.after(0, lambda: self._log(f"Downloading {model_id}..."))

            snapshot_download(
                repo_id=model_id,
                local_dir=str(models_dir),
                local_dir_use_symlinks=False,
            )

            self.after(0, lambda: self._log(f"Model downloaded to {models_dir}"))
        except ImportError:
            self.after(0, lambda: self._log("huggingface_hub not available, skipping model download"))
            self.after(0, lambda: self._log("The model will be downloaded on first use."))
        except Exception as e:
            self.after(0, lambda: self._log(f"Warning: Could not download model: {e}"))
            self.after(0, lambda: self._log("The model will be downloaded on first use."))

    def _save_settings(self):
        settings = {
            "temp_root": "C:/Temp/IACallMinute",
            "output_dir": "C:/Temp/IACallMinute",
            "bilingual": True,
            "log_enabled": False,
            "record_video": False,
            "screen_area": {"mode": "screen", "rect": None, "fps": 15},
            "autostop_minutes": 60,
            "autostop_time": "18:00",
            "save_transcript": True,
            "save_summary": False,
            "save_minutes": False,
            "setup_complete": True,
        }

        if self._stt_choice.get() == "offline":
            settings["stt"] = {
                "type": "offline",
                "model": self._whisper_size.get(),
                "api_key": "",
                "device": "auto",
            }
        else:
            settings["stt"] = {
                "type": "gemini",
                "model": "gemini-3.5-flash-lite",
                "api_key": self._api_key.get() if self._text_choice.get() == "gemini" else "",
            }

        text_type = self._text_choice.get()
        if text_type == "gemini":
            settings["text"] = {
                "type": "gemini",
                "model": "gemini-3.5-flash-lite",
                "api_key": self._api_key.get(),
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
                "api_key": self._api_key.get(),
                "api_url": "https://api.groq.com/openai/v1",
            }
        else:
            settings["text"] = {
                "type": "openai",
                "model": "gpt-4o-mini",
                "api_key": self._api_key.get(),
                "api_url": self._api_url.get(),
            }

        settings_file = APPDATA_DIR / "settings.json"
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

        self.after(0, lambda: self._log(f"Settings saved to {settings_file}"))

    def _create_desktop_shortcut(self):
        try:
            import winreg
            desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))

            exe_path = Path(self._install_dir.get()) / "IACallMinute.exe"
            if not exe_path.exists():
                for p in [Path.cwd(), Path(__file__).parent]:
                    candidate = p / "dist" / "IACallMinute.exe"
                    if candidate.exists():
                        exe_path = candidate
                        break

            shortcut_path = desktop / f"{APP_NAME}.lnk"

            ps_script = f'''
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{exe_path}"
$Shortcut.WorkingDirectory = "{exe_path.parent}"
$Shortcut.Description = "{APP_NAME} - Meeting Recording and Transcription"
$Shortcut.Save()
'''
            subprocess.run(["powershell", "-Command", ps_script],
                           capture_output=True, check=True)

            self.after(0, lambda: self._log(f"Shortcut created: {shortcut_path}"))
        except Exception as e:
            self.after(0, lambda: self._log(f"Warning: Could not create shortcut: {e}"))

    def _install_complete(self):
        self._current_step += 1
        self._show_step()

    def _install_error(self, error: str):
        self._status_label.config(text=f"Error: {error}")
        self._log(f"ERROR: {error}")
        self._next_btn.config(state="normal", text="Retry")
        messagebox.showerror("Installation Error",
                             f"An error occurred during installation:\n\n{error}\n\n"
                             "Check the log for details.")

    def _on_cancel(self):
        if messagebox.askyesno("Cancel Installation",
                               "Are you sure you want to cancel the installation?"):
            self.destroy()


def _existing_installation() -> bool:
    if (APPDATA_DIR / "settings.json").is_file():
        return True
    default_dir = Path.home() / "AppData" / "Local" / APP_NAME
    if (default_dir / "IACallMinute.exe").is_file():
        return True
    return False


def main():
    app = InstallerWizard()
    app.mainloop()


if __name__ == "__main__":
    main()
