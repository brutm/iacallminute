@echo off
REM Build dell'eseguibile IACallMinute (onefile, senza console)
cd /d "%~dp0"

python -m pip install --quiet pyinstaller
if errorlevel 1 goto pip_fail

REM PyInstaller con --noconfirm sovrascrive automaticamente l'exe esistente.
REM Non serve cancellare prima: evita race condition se AV/Defender blocca il file.

REM I modelli utente (whisper/argos/cuda_dlls) in dist\modelli NON devono
REM mai sparire o venire riscaricati: li metto da parte e li ripristino
REM dopo la build (PyInstaller non li tocca, ma qui nessun rischio)
if exist dist\modelli move /y dist\modelli dist\modelli.keep >nul

python -m PyInstaller --noconfirm --onefile --windowed ^
    --name IACallMinute ^
    --collect-all pyaudiowpatch ^
    --collect-all docx ^
    --collect-all faster_whisper ^
    --collect-all argostranslate ^
    --collect-all ctranslate2 ^
    --collect-all onnxruntime ^
    --collect-all av ^
    --hidden-import tkinter ^
    --hidden-import huggingface_hub ^
    --hidden-import tokenizers ^
    --hidden-import sentencepiece ^
    --exclude-module pytest ^
    --exclude-module _pytest ^
    --exclude-module py ^
    --exclude-module torch ^
    --exclude-module torchaudio ^
    --exclude-module torchvision ^
    --exclude-module stanza ^
    --exclude-module spacy ^
    --exclude-module cv2 ^
    --exclude-module pandas ^
    --exclude-module pyarrow ^
    --exclude-module scipy ^
    --exclude-module matplotlib ^
    --splash assets/splash.png ^
    --noupx ^
    main.py
set RC=%errorlevel%

REM ripristino dei modelli utente, in successo e in fallimento
if exist dist\modelli.keep (
    if exist dist\modelli rmdir /s /q dist\modelli
    move /y dist\modelli.keep dist\modelli >nul
)
if not %RC%==0 goto pyi_fail

if not exist dist\IACallMinute.exe goto missing

echo.
echo BUILD OK: dist\IACallMinute.exe
exit /b 0

:pip_fail
echo.
echo BUILD FALLITA - pip install pyinstaller
exit /b 1

:pyi_fail
echo.
echo BUILD FALLITA - errore PyInstaller
exit /b 1

:missing
echo.
echo BUILD FALLITA - dist\IACallMinute.exe non prodotto
exit /b 1
