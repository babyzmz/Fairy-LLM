param([string]$OutputPath)
$ErrorActionPreference = 'Stop'
if (-not [IO.Path]::IsPathRooted($OutputPath)) { throw 'An absolute output path is required' }
# Generates synthetic test speech locally. Never records microphone input.
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice.SetOutputToWaveFile($OutputPath)
    $voice.Speak('Hello Fairy. This is a local transcription test.')
} finally {
    $voice.Dispose()
}
