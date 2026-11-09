# Riproduce il crash del selftest: esegue ripetutamente e salva l'output
# completo (stdout+stderr) al primo exit code != 0.
$out = "C:\Progetti\IACallMinute\_crash"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Remove-Item "$out\run_*.txt" -ErrorAction SilentlyContinue
Set-Location C:\Progetti\IACallMinute
"avviato $(Get-Date -Format s)" | Set-Content "$out\status.txt"
for ($i = 1; $i -le 8; $i++) {
    $f = "$out\run_$i.txt"
    & python main.py --selftest --no-ai *> $f
    $code = $LASTEXITCODE
    "run $i exit=$code $(Get-Date -Format s)" | Add-Content "$out\status.txt"
    if ($code -ne 0) {
        "CRASH run $i" | Add-Content "$out\status.txt"
        Copy-Item $f "$out\CRASH.txt" -Force
        break
    }
}
"DONE $(Get-Date -Format s)" | Add-Content "$out\status.txt"
