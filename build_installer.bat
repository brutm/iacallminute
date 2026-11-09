@echo off
setlocal
cd /d "%~dp0"

echo Building IACallMinute Setup Installer...
echo.

python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name IACallMinute_Setup ^
    --distpath dist ^
    --workpath build\installer_work ^
    --specpath build ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    installer.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build successful! Installer: dist\IACallMinute_Setup.exe
) else (
    echo.
    echo Build failed with error %ERRORLEVEL%
)

pause
