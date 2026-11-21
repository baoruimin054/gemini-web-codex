param(
    [string]$Python = 'python',
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$environment = Join-Path $project '.artifact-venv'
$runtime = Join-Path $environment 'Scripts\python.exe'
$windowedRuntime = Join-Path $environment 'Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $runtime)) {
    & $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
    & $Python -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}

& $runtime -m pip --version
if ($LASTEXITCODE -ne 0) {
    & $runtime -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip.' }
}
& $runtime -m pip install -r (Join-Path $project 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Python dependencies.' }
& $runtime -c 'import tkinter, PIL, fitz, imageio_ffmpeg, docx'
if ($LASTEXITCODE -ne 0) { throw 'The Python installation must provide Tkinter and the listed dependencies.' }

if (-not $NoShortcut) {
    if (-not (Test-Path -LiteralPath $windowedRuntime)) { throw 'Pythonw.exe is missing from the environment.' }
    $desktop = [Environment]::GetFolderPath('Desktop')
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Gemini Codex Web.lnk'))
    $link.TargetPath = $windowedRuntime
    $link.Arguments = '"' + (Join-Path $project 'desktop_launcher.pyw') + '"'
    $link.WorkingDirectory = $project
    $link.IconLocation = (Join-Path $project 'Gemini-Codex.ico') + ',0'
    $link.Save()
    Write-Host 'Installed. Open the Gemini Codex Web desktop shortcut.'
}
