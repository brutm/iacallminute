# Pulisce gli artefatti di sviluppo (NON tocca dist\IACallMinute.exe).
# Uso:  powershell -ExecutionPolicy Bypass -File tools\clean_build.ps1
$ErrorActionPreference = "SilentlyContinue"
Set-Location C:\Progetti\IACallMinute

$targets = @("build", "_verify", "_crash", "__pycache__",
             "build_err.log", "build_log.txt", "build_out.log")
$freed = 0
foreach ($t in $targets) {
    if (Test-Path $t) {
        $s = (Get-ChildItem $t -Recurse -File | Measure-Object -Sum Length).Sum
        Remove-Item $t -Recurse -Force
        if ($s) { $freed += $s }
        Write-Output "rimosso $t ($([math]::Round($s/1MB,1)) MB)"
    }
}
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | ForEach-Object {
    $s = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object -Sum Length).Sum
    Remove-Item $_.FullName -Recurse -Force
    $freed += $s
}

# sessioni temporanee non salvate (anche di app chiuse in modo anomalo)
$tmp = "C:\Temp\IACallMinute"
if (Test-Path $tmp) {
    Get-ChildItem $tmp -Directory | Where-Object {
        $_.Name -like "session_*" -or $_.Name -like "selftest_*"
    } | ForEach-Object {
        $s = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object -Sum Length).Sum
        Remove-Item $_.FullName -Recurse -Force
        $freed += $s
        Write-Output "rimosso $($_.Name) ($([math]::Round($s/1MB,1)) MB)"
    }
}

Write-Output "TOTALE LIBERATI: $([math]::Round($freed/1MB,1)) MB"
Write-Output "dist\IACallMinute.exe resta intatto (exe distribuito)"