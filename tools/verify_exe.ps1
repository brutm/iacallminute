$out = "C:\Progetti\IACallMinute\_verify"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$exe = "C:\Progetti\IACallMinute\dist\IACallMinute.exe"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
function Snap($name) {
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $bmp.Save("$out\$name.png")
    $g.Dispose(); $bmp.Dispose()
    "snap $name"
}
function Ui-Proc {   # processo con la finestra vera, avvio completato
    Get-Process IACallMinute -EA SilentlyContinue |
        Where-Object { $_.MainWindowTitle -like 'IACallMinute 1*' }
}
function All-Proc {
    @(Get-Process IACallMinute -EA SilentlyContinue)
}

$fail = 0
Get-Process IACallMinute -EA SilentlyContinue | Stop-Process -Force
Start-Sleep 1

# --- 1. selftest dell'exe ------------------------------------------------
$p = Start-Process $exe -ArgumentList '--selftest','--no-ai' -PassThru -Wait
"selftest exit=$($p.ExitCode)"
$tail = Get-Content "$env:APPDATA\IACallMinute\logs\selftest_report.txt" -Tail 2
$tail | ForEach-Object { "  report: $_" }
if ($p.ExitCode -ne 0 -or ($tail -join ' ') -notmatch '0 falliti') {
    "FAIL selftest exe"; $fail = 1
}
if ((All-Proc).Count -ne 0) { "FAIL residui dopo selftest"; $fail = 1 }

# --- 2. splash + finestra principale -------------------------------------
$g = Start-Process $exe -PassThru
Start-Sleep 1
Snap "t1s_splash"
Start-Sleep 4
Snap "t5s"
$deadline = (Get-Date).AddSeconds(45)
$ui = $null
while ((Get-Date) -lt $deadline) {
    $ui = Ui-Proc | Select-Object -First 1
    if ($ui) { break }
    Start-Sleep 0.5
}
Start-Sleep 1
Snap "t_gui"
if (-not $ui) {
    "FAIL finestra principale mai visibile"; $fail = 1
} else {
    "finestra principale: '$($ui.MainWindowTitle)' (pid $($ui.Id))"
}
"processi in esecuzione: $((All-Proc).Count) (bootloader + figlio attesi 2)"

# --- 3. secondo avvio respinto -------------------------------------------
$nBefore = (All-Proc).Count
$d = Start-Process $exe -PassThru -Wait
"secondo avvio exit=$($d.ExitCode)"
Start-Sleep 2
$nAfter = (All-Proc).Count
"processi: $nBefore -> $nAfter (invariati = respinto)"
if ($d.ExitCode -ne 0 -or $nAfter -ne $nBefore) {
    "FAIL doppio avvio"; $fail = 1
}

# --- 4. chiusura + residui -------------------------------------------------
if ($ui -and -not $ui.HasExited) {
    $okClose = $ui.CloseMainWindow()
    "CloseMainWindow -> $okClose"
}
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and (All-Proc).Count -gt 0) {
    Start-Sleep 1
}
Start-Sleep 2
$n2 = (All-Proc).Count
"processi residui: $n2 (atteso 0)"
if ($n2 -ne 0) {
    All-Proc | ForEach-Object { "  residuo pid $($_.Id) '$($_.MainWindowTitle)'" }
    "FAIL residui"; $fail = 1
    All-Proc | Stop-Process -Force
}

if ($fail -eq 0) { "VERIFY OK" } else { "VERIFY FAIL" }
exit $fail
