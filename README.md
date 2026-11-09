# IACallMinute

**Real-time meeting transcription and summarization tool for Windows.**

Captures system audio (+ optional microphone), transcribes live during calls (Teams, Zoom, Meet, Webex...), optionally records video, and generates AI-powered summaries and meeting minutes in Markdown and Word format.

[English](#features) | [Italiano](#funzionalità)

---

## Features

- **Live transcription** during calls with real-time display
- **Screen recording** with embedded audio (MP4)
- **AI summaries and meeting minutes** in Markdown and Word (.docx)
- **Multiple AI providers**: Google Gemini, GitHub Copilot, Groq, OpenAI-compatible endpoints
- **Offline transcription** with Whisper (no internet required, no API costs)
- **Automatic stop** at timer expiry or when call ends
- **Bilingual output**: original language + translation
- **Context-aware**: add project notes and folder analysis for better summaries

## Installation

### Option 1: Setup Wizard (Recommended)

1. Download `IACallMinute_Setup.exe` from [Releases](https://github.com/brutm/iacallminute/releases)
2. Run the installer and follow the wizard
3. Choose your transcription method (Whisper offline or cloud API)
4. Configure your AI provider for summaries

### Option 2: Portable Executable

1. Download `IACallMinute.exe` from [Releases](https://github.com/brutm/iacallminute/releases)
2. Run directly - no installation required
3. Configure providers in **Settings** on first launch

### Option 3: From Source

```bash
git clone https://github.com/brutm/iacallminute.git
cd iacallminute
pip install -r requirements.txt
python main.py
```

## Requirements

- Windows 10/11 (WASAPI audio capture)
- **Optional**: ffmpeg in PATH (for MP3 export; without it, saves as WAV)
- **For offline transcription**: Whisper model downloaded on first use (~500 MB for recommended "small" model)

## Quick Start

1. **Configure** - Click the settings button and set up your AI provider:
   - **Transcription**: Whisper offline (free, private) or Google Gemini (cloud)
   - **Summaries**: Google Gemini, GitHub Copilot, Groq (free tier), or custom endpoint

2. **Start Recording** - Click "START" to begin capturing audio and transcribing live

3. **Stop & Save** - Click "STOP" when done, then save:
   - Transcription (Markdown)
   - Audio recording (MP3/WAV)
   - Video with audio (MP4, if enabled)
   - AI-generated summary or minutes

## AI Providers

### Transcription (Speech-to-Text)

| Provider | Cost | Features |
|----------|------|----------|
| **Whisper Offline** (recommended) | Free | Local processing, no internet needed, private |
| Google Gemini | Free tier | Cloud-based, fast, good accuracy |
| OpenAI-compatible | Varies | Groq, OpenAI, local LLMs |

### Summaries & Minutes (Text)

| Provider | Cost | Features |
|----------|------|----------|
| **Google Gemini** | Free tier | Get key at [aistudio.google.com](https://aistudio.google.com/apikey) |
| **GitHub Copilot** | Subscription | Uses your existing Copilot subscription, browser login |
| **Groq** | Free tier | Very fast, get key at [console.groq.com](https://console.groq.com/keys) |
| OpenAI-compatible | Varies | Any compatible endpoint |

## Key Features

### Automatic Stop
- **Timer**: Stop after N minutes or at specific time
- **Call End Detection**: Automatically stops when Teams/Zoom/Webex/etc. releases the microphone
- **Auto-save**: Saves recording, transcription, summary, and minutes when timer expires

### Screen Recording
- Record the entire screen, a specific window, or custom region
- Output: MP4 with embedded audio
- Optional video analysis: AI describes what appeared on screen

### Offline Mode
- Whisper models run locally (no internet after initial download)
- Argos Translate for offline translation
- GPU acceleration supported (CUDA)

## Privacy

- All recordings stay **local** on your machine
- Only speech segments (~1-4 seconds) are sent to cloud providers for transcription
- Offline mode keeps everything on your device
- API keys stored only in `%APPDATA%\IACallMinute\settings.json`

## Building from Source

```bash
# Build main executable
build_exe.bat

# Build installer
build_installer.bat
```

## License

MIT License - see [LICENSE](LICENSE) for details.

---

# Italiano

## Funzionalità

- **Trascrizione in tempo reale** durante le chiamate con visualizzazione live
- **Registrazione schermo** con audio incorporato (MP4)
- **Riassunti e minute** generate dall'AI in Markdown e Word (.docx)
- **Diversi provider AI**: Google Gemini, GitHub Copilot, Groq, endpoint OpenAI-compatibili
- **Trascrizione offline** con Whisper (senza internet, senza costi API)
- **Arresto automatico** allo scadere del timer o alla fine della chiamata
- **Output bilingue**: lingua originale + traduzione
- **Contesto**: aggiungi note e analisi cartella progetto per riassunti migliori

## Installazione

### Opzione 1: Wizard di Setup (Consigliato)

1. Scarica `IACallMinute_Setup.exe` da [Releases](https://github.com/brutm/iacallminute/releases)
2. Esegui l'installer e segui il wizard
3. Scegli il metodo di trascrizione (Whisper offline o API cloud)
4. Configura il provider AI per i riassunti

### Opzione 2: Eseguibile Portatile

1. Scarica `IACallMinute.exe` da [Releases](https://github.com/brutm/iacallminute/releases)
2. Esegui direttamente - nessuna installazione richiesta
3. Configura i provider in **Configurazione** al primo avvio

### Opzione 3: Da Sorgenti

```bash
git clone https://github.com/brutm/iacallminute.git
cd iacallminute
pip install -r requirements.txt
python main.py
```

## Requisiti

- Windows 10/11 (cattura audio WASAPI)
- **Opzionale**: ffmpeg nel PATH (per esportazione MP3; senza, salva in WAV)
- **Per trascrizione offline**: modello Whisper scaricato al primo uso (~500 MB per il modello "small" consigliato)

## Guida Rapida

1. **Configura** - Clicca il pulsante impostazioni e configura il provider AI:
   - **Trascrizione**: Whisper offline (gratuito, privato) o Google Gemini (cloud)
   - **Riassunti**: Google Gemini, GitHub Copilot, Groq (tier gratuito), o endpoint personalizzato

2. **Avvia Registrazione** - Clicca "AVVIA" per iniziare a catturare audio e trascrivere in tempo reale

3. **Ferma e Salva** - Clicca "FERMA" quando hai finito, poi salva:
   - Trascrizione (Markdown)
   - Registrazione audio (MP3/WAV)
   - Video con audio (MP4, se abilitato)
   - Riassunto o minute generate dall'AI

## Provider AI

### Trascrizione (Speech-to-Text)

| Provider | Costo | Caratteristiche |
|----------|-------|-----------------|
| **Whisper Offline** (consigliato) | Gratuito | Elaborazione locale, no internet, privato |
| Google Gemini | Tier gratuito | Cloud, veloce, buona accuratezza |
| OpenAI-compatibile | Varia | Groq, OpenAI, LLM locali |

### Riassunti e Minute (Testo)

| Provider | Costo | Caratteristiche |
|----------|-------|-----------------|
| **Google Gemini** | Tier gratuito | Chiave da [aistudio.google.com](https://aistudio.google.com/apikey) |
| **GitHub Copilot** | Abbonamento | Usa il tuo abbonamento Copilot, login browser |
| **Groq** | Tier gratuito | Molto veloce, chiave da [console.groq.com](https://console.groq.com/keys) |
| OpenAI-compatibile | Varia | Qualsiasi endpoint compatibile |

## Funzionalità Principali

### Arresto Automatico
- **Timer**: Ferma dopo N minuti o a un'ora specifica
- **Rilevamento Fine Chiamata**: Si ferma automaticamente quando Teams/Zoom/Webex/etc. rilascia il microfono
- **Salvataggio automatico**: Salva registrazione, trascrizione, riassunto e minute allo scadere del timer

### Registrazione Schermo
- Registra l'intero schermo, una finestra specifica, o una regione personalizzata
- Output: MP4 con audio incorporato
- Analisi video opzionale: l'AI descrive cosa è apparso a schermo

### Modalità Offline
- I modelli Whisper girano localmente (no internet dopo il download iniziale)
- Argos Translate per traduzione offline
- Accelerazione GPU supportata (CUDA)

## Privacy

- Tutte le registrazioni restano **in locale** sul tuo computer
- Solo i segmenti di parlato (~1-4 secondi) vengono inviati ai provider cloud per la trascrizione
- La modalità offline mantiene tutto sul tuo dispositivo
- Le chiavi API sono salvate solo in `%APPDATA%\IACallMinute\settings.json`

## Build da Sorgenti

```bash
# Build eseguibile principale
build_exe.bat

# Build installer
build_installer.bat
```

## Licenza

MIT License - vedi [LICENSE](LICENSE) per i dettagli.

---

## Troubleshooting / Risoluzione Problemi

| Problem / Problema | Solution / Soluzione |
|-------------------|----------------------|
| No transcription / Nessuna trascrizione | Check the **level** indicator; if flat, select a different **System Audio** device | Controlla l'indicatore **livello**; se piatto, seleziona un diverso dispositivo **Audio sistema** |
| Microphone not captured / Microfono non catturato | Allow microphone access in Windows Settings → Privacy → Microphone | Consenti l'accesso al microfono in Impostazioni → Privacy → Microfono |
| No MP3 output / Nessun MP3 | Install ffmpeg (`winget install ffmpeg`); without it, saves as WAV | Installa ffmpeg (`winget install ffmpeg`); senza, salva in WAV |
| 429 quota error / Errore quota 429 | The app retries automatically; wait or switch to a different model | L'app riprova automaticamente; attendi o cambia modello |
| Recording not saved / Registrazione non salvata | Press **Save recording...** before closing the app | Premi **Salva registrazione...** prima di chiudere l'app |

## Support

- **Issues**: [GitHub Issues](https://github.com/brutm/iacallminute/issues)
- **Discussions**: [GitHub Discussions](https://github.com/brutm/iacallminute/discussions)
