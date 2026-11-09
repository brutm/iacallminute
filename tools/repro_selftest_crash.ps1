$env:PYTHONIOENCODING='utf-8'
Set-Location C:\Progetti\IACallMinute
New-Item -ItemType Directory -Force -Path _crash | Out-Null
for ($i = 1; $i -le 8; $i++) {
    $err = "_crash\st_err_$i.txt"
    $out = "_crash\st_out_$i.txt"
    cmd /c "python main.py --selftest --no-ai > $out 2> $err"
    $code = $LASTEXITCODE
    Write-Output "run $i exit=$code"
    if ($code -ne 0) {
        Write-Output "CRASH riprodotto in run $i (stderr:)"
        Get-Content $err -TotalCount 40
        exit 0
    }
}
Write-Output "nessun crash in 8 run"
exit 1
