$env:PYTHONIOENCODING='utf-8'
Set-Location C:\Progetti\IACallMinute

$steps = @(
    "python -m py_compile main.py app/config.py app/autostop.py app/power.py app/callwatch.py app/ui/window.py app/ui/settings.py app/ui/tkutil.py app/ui/progress.py app/ui/deferred.py app/ai/deferred.py app/splash.py app/singleinstance.py app/ui/hwmon.py",
    "python -m py_compile app/ai/offline.py app/ai/service.py app/ai/gemini.py app/ai/openai_compat.py app/ai/prompts.py app/storage/recorder.py app/storage/screenrec.py",
    "python tools/test_tkutil.py",
    "python tools/test_crashlog.py",
    "python tools/test_paused_sem.py",
    "python tools/test_singleinstance.py",
    "python tools/test_splash.py",
    "python tools/test_splash_root.py",
    "python tools/test_presets.py",
    "python tools/test_models_dir.py",
    "python tools/test_gpu.py",
    "python tools/test_hwmon.py",
    "python tools/test_restart.py",
    "python tools/test_stop_overlay.py",
    "python tools/test_autostop.py",
    "python tools/test_stop_drain.py",
    "python tools/test_purge_temp.py",
    "python tools/test_deferred.py",
    "python tools/test_viewer.py",
    "python tools/test_fault.py",
    "python tools/test_argos_nostanza.py",
    "python main.py --selftest --no-ai"
)

$fail = 0
foreach ($s in $steps) {
    Write-Output ">>> $s"
    cmd /c $s
    if ($LASTEXITCODE -ne 0) {
        Write-Output "FAILED ($LASTEXITCODE): $s"
        $fail = 1
        break
    }
}
if ($fail -eq 0) { Write-Output "BATTERY OK" }
exit $fail
