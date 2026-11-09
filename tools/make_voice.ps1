Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$out = Join-Path $env:TEMP 'iacm_voice.wav'
$s.SetOutputToWaveFile($out)
$s.Speak('Buongiorno a tutti, iniziamo la riunione di oggi parlando del progetto IACallMinute.')
$s.Dispose()
Write-Output $out
